import argparse
import asyncio
import gzip
import hashlib
import json
import logging
import os
import re
import statistics
import unicodedata
from collections import deque
from contextlib import asynccontextmanager
from copy import deepcopy
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from openai import AsyncOpenAI

from local_api_config import get_config_list, get_config_value

JUDGE_MODEL = (
    os.getenv("JUDGE_MODEL")
    or os.getenv("QWEN_MODEL")
    or get_config_value("JUDGE_MODEL", "QWEN_MODEL", default="hjl_Qwen3.6-27B")
)
JUDGE_BASE_URL = (
    os.getenv("JUDGE_BASE_URL")
    or os.getenv("QWEN_BASE_URL")
    or get_config_value("JUDGE_BASE_URL", "QWEN_BASE_URL", default="http://127.0.0.1:18011/v1")
)
ANSWER_MODEL = (
    os.getenv("ANSWER_MODEL")
    or os.getenv("QWEN_MODEL")
    or get_config_value("ANSWER_MODEL", "QWEN_MODEL", default="")
)
ANSWER_BASE_URL = (
    os.getenv("ANSWER_BASE_URL")
    or os.getenv("QWEN_BASE_URL")
    or get_config_value("ANSWER_BASE_URL", "QWEN_BASE_URL", default="")
)
GPT_JUDGE_MODEL = (
    os.getenv("GPT_JUDGE_MODEL")
    or os.getenv("GPT_MODEL")
    or get_config_value("GPT_JUDGE_MODEL", "GPT_MODEL", "QA_MODEL", default="")
)
GPT_JUDGE_BASE_URL = (
    os.getenv("GPT_JUDGE_BASE_URL")
    or os.getenv("OPENAI_BASE_URL")
    or get_config_value("GPT_JUDGE_BASE_URL", "OPENAI_BASE_URL", "BASE_URL", default="")
)


ANSWER_PLACEHOLDER = "<<<待评答案>>"
REQUEST_TIMEOUT_SECONDS = 180.0
EVALUATION_PROTOCOL = "dual_judge_parallel_v1"
DEFAULT_ANSWER_TRIALS = 3
DEFAULT_QWEN_JUDGE_REPEATS = 2
DEFAULT_GPT_JUDGE_REPEATS = 2


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)


def parse_api_keys(cli_keys: Optional[List[str]] = None) -> List[str]:
    if cli_keys:
        keys = [key.strip() for key in cli_keys if key and key.strip()]
        if keys:
            return keys
    raw = (
        os.getenv("JUDGE_API_KEYS")
        or os.getenv("QWEN_API_KEYS")
        or os.getenv("QWEN_API_KEY")
        or os.getenv("OPENAI_API_KEYS")
        or os.getenv("OPENAI_API_KEY")
        or ""
    )
    keys = [part.strip() for part in raw.split(",") if part.strip()]
    if keys:
        return keys
    keys = get_config_list(
        "JUDGE_API_KEYS",
        "QWEN_API_KEYS",
        "QWEN_API_KEY",
        "OPENAI_API_KEYS",
        "OPENAI_API_KEY",
        "API_KEYS",
    )
    return keys or ["EMPTY_KEY"]


def resolve_answer_api_key(cli_key: str = "") -> str:
    text = (cli_key or "").strip()
    if text:
        return text
    raw = (
        os.getenv("ANSWER_API_KEY")
        or os.getenv("QWEN_API_KEY")
        or os.getenv("OPENAI_API_KEY")
        or ""
    )
    keys = [part.strip() for part in raw.split(",") if part.strip()]
    if keys:
        return keys[0]
    config_keys = get_config_list(
        "ANSWER_API_KEYS",
        "ANSWER_API_KEY",
        "QWEN_API_KEYS",
        "QWEN_API_KEY",
        "OPENAI_API_KEYS",
        "OPENAI_API_KEY",
        "API_KEYS",
    )
    return config_keys[0] if config_keys else ""


def parse_gpt_judge_api_keys(cli_keys: Optional[List[str]] = None) -> List[str]:
    """Resolve GPT judge credentials without falling back to the Qwen service key."""
    if cli_keys:
        keys = [key.strip() for key in cli_keys if key and key.strip()]
        if keys:
            return keys
    raw = (
        os.getenv("GPT_JUDGE_API_KEYS")
        or os.getenv("GPT_JUDGE_API_KEY")
        or os.getenv("GPT_API_KEYS")
        or os.getenv("HIAPI_KEYS_BIG")
        or os.getenv("OPENAI_API_KEYS")
        or os.getenv("OPENAI_API_KEY")
        or ""
    )
    keys = [part.strip() for part in raw.split(",") if part.strip()]
    if keys:
        return keys
    keys = get_config_list(
        "GPT_JUDGE_API_KEYS",
        "GPT_JUDGE_API_KEY",
        "GPT_API_KEYS",
        "HIAPI_KEYS_BIG",
        "OPENAI_API_KEYS",
        "OPENAI_API_KEY",
        "API_KEYS",
    )
    return keys or ["EMPTY_KEY"]


class FairRequestPool:
    """Bound actual in-flight calls and rotate grants across active samples."""

    def __init__(self, limit: int, name: str):
        if limit < 1:
            raise ValueError(f"{name} request pool limit must be >= 1")
        self.limit = int(limit)
        self.name = name
        self.active = 0
        self.peak_active = 0
        self._waiters = deque()
        self._lock = asyncio.Lock()
        self._last_granted_sample: Optional[str] = None

    def _next_waiter_index(self) -> int:
        if not self._waiters:
            return -1
        if self._last_granted_sample is None:
            return 0
        for index, (sample_key, _) in enumerate(self._waiters):
            if sample_key != self._last_granted_sample:
                return index
        return 0

    def _dispatch_locked(self) -> None:
        while self.active < self.limit and self._waiters:
            index = self._next_waiter_index()
            self._waiters.rotate(-index)
            sample_key, future = self._waiters.popleft()
            self._waiters.rotate(index)
            if future.cancelled():
                continue
            self.active += 1
            self.peak_active = max(self.peak_active, self.active)
            self._last_granted_sample = sample_key
            future.set_result(None)

    async def acquire(self, sample_key: str) -> None:
        loop = asyncio.get_running_loop()
        future = loop.create_future()
        normalized_key = str(sample_key or "unknown")
        async with self._lock:
            self._waiters.append((normalized_key, future))
            self._dispatch_locked()
        try:
            await future
        except asyncio.CancelledError:
            async with self._lock:
                still_waiting = any(waiter_future is future for _, waiter_future in self._waiters)
                if still_waiting:
                    self._waiters = deque(
                        (key, waiter_future)
                        for key, waiter_future in self._waiters
                        if waiter_future is not future
                    )
                else:
                    self.active = max(0, self.active - 1)
                self._dispatch_locked()
            raise

    async def release(self) -> None:
        async with self._lock:
            self.active = max(0, self.active - 1)
            self._dispatch_locked()

    @asynccontextmanager
    async def request(self, sample_key: str):
        await self.acquire(sample_key)
        try:
            yield
        finally:
            await self.release()


def extract_answer(resp) -> str:
    choices = getattr(resp, "choices", None)
    if choices:
        first_choice = choices[0]
        message = getattr(first_choice, "message", None)
        content = getattr(message, "content", "")
        return (content or "").strip()

    if hasattr(resp, "model_dump"):
        payload = resp.model_dump()
        choices = payload.get("choices")
        if choices:
            message = choices[0].get("message", {})
            content = message.get("content", "")
            return (content or "").strip()

    if isinstance(resp, str):
        payload = resp
        if payload.startswith("data:"):
            payload = payload[len("data:"):].strip()
        parsed = json.loads(payload)
        return (parsed["choices"][0]["message"]["content"] or "").strip()

    raise TypeError(f"Unsupported or empty response type: {type(resp)}")


class RotatingAPIClient:
    """
    支持自动切换 API Key 的 OpenAI 客户端包装器。
    当遇到 401 令牌额度用尽错误时，自动切换到下一个 key。
    """
    def __init__(self, base_url: str, api_keys: List[str]):
        if not api_keys:
            raise ValueError("api_keys 不能为空")
        self.base_url = base_url
        self.api_keys = api_keys
        self.current_key_index = 0
        self.client: Optional[AsyncOpenAI] = None
        self._lock = asyncio.Lock()
        self._init_client()

    def _init_client(self):
        current_key = self.api_keys[self.current_key_index]
        self.client = AsyncOpenAI(
            api_key=current_key,
            base_url=self.base_url,
            timeout=REQUEST_TIMEOUT_SECONDS
        )
        logger.info(
            f"使用评分 API Key [{self.current_key_index + 1}/{len(self.api_keys)}]: "
            f"{current_key[:8]}..."
        )

    def _is_token_exhausted_error(self, error: Exception) -> bool:
        error_str = str(error)
        return (
            "401" in error_str and
            ("TokenStatusExhausted" in error_str or "令牌额度已用尽" in error_str)
        )

    async def switch_to_next_key(self) -> bool:
        async with self._lock:
            self.current_key_index += 1
            if self.current_key_index >= len(self.api_keys):
                logger.error("所有评分 API Key 额度已用尽")
                return False
            self._init_client()
            return True

    async def chat_completions_create(self, **kwargs):
        max_key_switches = len(self.api_keys)
        for _ in range(max_key_switches):
            try:
                return await self.client.chat.completions.create(**kwargs)
            except Exception as e:
                if self._is_token_exhausted_error(e):
                    logger.warning(f"评分 API Key [{self.current_key_index + 1}] 额度用尽: {str(e)[:100]}")
                    if await self.switch_to_next_key():
                        continue
                    raise Exception("所有评分 API Key 额度已用尽") from e
                raise
        raise Exception("所有评分 API Key 额度已用尽")


class AnswerLLMClient:
    """用于自由配置的待评答案模型。"""
    def __init__(
        self,
        base_url: str,
        api_key: str,
        model: str,
        temperature: Optional[float] = None,
        top_p: Optional[float] = None,
    ):
        self.base_url = base_url
        self.api_key = api_key if api_key else "EMPTY_KEY"
        self.model = model
        self.temperature = temperature
        self.top_p = top_p
        self.client = AsyncOpenAI(
            api_key=self.api_key,
            base_url=self.base_url,
            timeout=REQUEST_TIMEOUT_SECONDS
        )

    async def generate_answer(self, question: str) -> str:
        request: Dict[str, Any] = {
            "model": self.model,
            "messages": [
                {"role": "user", "content": question}
            ],
        }
        if self.temperature is not None:
            request["temperature"] = self.temperature
        if self.top_p is not None:
            request["top_p"] = self.top_p
        response = await self.client.chat.completions.create(
            **request
        )
        return extract_answer(response)


def extract_json_from_response(response_text: str) -> str:
    """从模型响应中提取 JSON 对象或代码块。"""
    response_text = response_text.strip()
    try:
        json.loads(response_text)
        return response_text
    except json.JSONDecodeError:
        pass

    json_match = re.search(r"```json\s*([\s\S]+?)\s*```", response_text)
    if json_match:
        return json_match.group(1).strip()

    code_match = re.search(r"```\s*([\s\S]+?)\s*```", response_text)
    if code_match:
        return code_match.group(1).strip()

    object_start, object_end = response_text.find("{"), response_text.rfind("}")
    if object_start != -1 and object_end != -1 and object_end > object_start:
        return response_text[object_start:object_end + 1].strip()

    raise ValueError("无法从评分响应中提取有效 JSON")


def loads_json_with_repair(json_str: str) -> Any:
    """解析评分 JSON；仅做保守修复。"""
    try:
        return json.loads(json_str)
    except json.JSONDecodeError:
        repaired = re.sub(r",\s*([\]}])", r"\1", json_str.strip())
        decoder = json.JSONDecoder()
        try:
            obj, _ = decoder.raw_decode(repaired.lstrip())
            return obj
        except Exception:
            object_start, object_end = repaired.find("{"), repaired.rfind("}")
            if object_start != -1 and object_end != -1 and object_end > object_start:
                return json.loads(repaired[object_start:object_end + 1])
            raise


def _rubric_title(rubric_item: Dict[str, Any], index: int) -> str:
    title = rubric_item.get("title")
    if not isinstance(title, str) or not title.strip():
        raise ValueError(f"rubric 第 {index + 1} 条缺少非空 title，无法安全对齐评分结果")
    return title.strip()


TITLE_QUOTE_TRANSLATION = str.maketrans({
    "“": '"',
    "”": '"',
    "„": '"',
    "＂": '"',
    "‘": '"',
    "’": '"',
    "‚": '"',
    "＇": '"',
    "'": '"',
    "`": '"',
    "´": '"',
    "«": '"',
    "»": '"',
    "「": '"',
    "」": '"',
    "『": '"',
    "』": '"',
})


def _canonical_title_for_matching(title: str) -> str:
    normalized = unicodedata.normalize("NFKC", title).translate(TITLE_QUOTE_TRANSLATION)
    return re.sub(r"\s+", " ", normalized).strip()


def _parse_awarded_score(awarded_raw: Any) -> int:
    try:
        if isinstance(awarded_raw, bool):
            return 0
        if isinstance(awarded_raw, (int, float)):
            return int(round(float(awarded_raw)))
        match = re.search(r"-?\d+(?:\.\d+)?", str(awarded_raw))
        return int(round(float(match.group(0)))) if match else 0
    except Exception:
        return 0


def _validate_rubric_titles(rubric: List[Dict[str, Any]]) -> List[str]:
    titles = [_rubric_title(item, index) for index, item in enumerate(rubric)]
    duplicate_titles = sorted({title for title in titles if titles.count(title) > 1})
    if duplicate_titles:
        raise ValueError(f"rubric 存在重复 title，无法按 title 安全对齐: {duplicate_titles}")
    canonical_titles = [_canonical_title_for_matching(title) for title in titles]
    duplicate_canonical_titles = sorted({
        titles[index]
        for index, canonical_title in enumerate(canonical_titles)
        if canonical_titles.count(canonical_title) > 1
    })
    if duplicate_canonical_titles:
        raise ValueError(f"rubric 存在规范化后重复 title，无法按 title 安全对齐: {duplicate_canonical_titles}")
    return titles


def normalize_item_scores(item_scores: Any, rubric: List[Dict[str, Any]]) -> Tuple[List[Dict[str, Any]], int]:
    """按 rubric title 校验并清洗 item_scores，计算总分。"""
    if not isinstance(item_scores, list):
        raise ValueError("评分结果中的 item_scores 必须为数组")

    rubric_titles = _validate_rubric_titles(rubric)
    if len(item_scores) != len(rubric):
        raise ValueError(f"评分结果 item_scores 数量为 {len(item_scores)}，rubric 数量为 {len(rubric)}")

    score_by_title: Dict[str, Dict[str, Any]] = {}
    raw_score_title_by_key: Dict[str, str] = {}
    for index, raw_item in enumerate(item_scores):
        if not isinstance(raw_item, dict):
            raise ValueError(f"评分结果 item_scores[{index}] 必须为对象")
        title = raw_item.get("title")
        if not isinstance(title, str) or not title.strip():
            raise ValueError(f"评分结果 item_scores[{index}] 缺少非空 title")
        title = title.strip()
        canonical_title = _canonical_title_for_matching(title)
        if canonical_title in score_by_title:
            raise ValueError(f"评分结果存在重复 title: {title}")
        score_by_title[canonical_title] = raw_item
        raw_score_title_by_key[canonical_title] = title

    expected_title_keys = {_canonical_title_for_matching(title) for title in rubric_titles}
    actual_title_keys = set(score_by_title)
    missing_titles = [
        title
        for title in rubric_titles
        if _canonical_title_for_matching(title) not in actual_title_keys
    ]
    extra_titles = [
        raw_score_title_by_key[title_key]
        for title_key in actual_title_keys
        if title_key not in expected_title_keys
    ]
    if missing_titles or extra_titles:
        raise ValueError(
            "评分结果 title 与 rubric 不一致: "
            f"missing={missing_titles}, extra={extra_titles}"
        )

    normalized_scores = []
    total_awarded = 0

    for index, rubric_item in enumerate(rubric):
        title = rubric_titles[index]
        weight = int(rubric_item.get("weight", 0) or 0)
        raw_item = score_by_title[_canonical_title_for_matching(title)]
        awarded_raw = raw_item.get("awarded", 0)
        brief_reason = raw_item.get("brief_reason", "")

        awarded = _parse_awarded_score(awarded_raw)
        if weight < 0:
            # 负分项（扣分项）：awarded 应在 [weight, 0] 之间
            awarded = max(weight, min(0, awarded))
        else:
            # 正分项：awarded 应在 [0, weight] 之间
            awarded = max(0, min(weight, awarded))
        total_awarded += awarded

        if not isinstance(brief_reason, str):
            brief_reason = str(brief_reason)

        normalized_scores.append({
            "title": title,
            "weight": weight,
            "awarded": awarded,
            "brief_reason": brief_reason.strip()
        })

    return normalized_scores, total_awarded


def build_scoring_prompt(score_prompt: str, answer_text: str) -> str:
    if ANSWER_PLACEHOLDER not in score_prompt:
        raise ValueError(f"score_prompt 中缺少占位符 {ANSWER_PLACEHOLDER}")
    return score_prompt.replace(ANSWER_PLACEHOLDER, answer_text)


def compute_score_rate(scoring_result: Dict[str, Any]) -> Optional[float]:
    try:
        awarded = float(scoring_result.get("total_awarded", 0) or 0)
        possible = float(scoring_result.get("total_possible", 0) or 0)
    except (TypeError, ValueError):
        return None
    if possible <= 0:
        return None
    score_rate = awarded / possible
    if score_rate < 0:
        return 0.0
    if score_rate > 1:
        return 1.0
    return score_rate


def ensure_sample_identity(item: Dict[str, Any]) -> None:
    if item.get("sample_id") is not None:
        return
    for field in ("index", "id"):
        value = item.get(field)
        if value is not None and str(value).strip():
            item["sample_id"] = str(value).strip()
            return


def attach_score_rate(item: Dict[str, Any]) -> None:
    scoring_result = item.get("scoring_result")
    if not isinstance(scoring_result, dict):
        return
    score_rate = compute_score_rate(scoring_result)
    if score_rate is not None:
        item["score_rate"] = score_rate


class ScoringProcessor:
    def __init__(
        self,
        judge_client: RotatingAPIClient,
        judge_model: str,
        answer_mode: str,
        max_concurrent: int = 20,
        max_retries: int = 3,
        answer_client: Optional[AnswerLLMClient] = None,
        answer_model_name: str = "",
        force_generate_answer: bool = False,
        judge_temperature: float = 0.0,
        gpt_judge_client: Optional[RotatingAPIClient] = None,
        gpt_judge_model: str = "",
        gpt_judge_temperature: float = 0.0,
        answer_trials: int = 1,
        qwen_judge_repeats: int = 1,
        gpt_judge_repeats: int = 0,
        qwen_max_concurrent: int = 20,
        gpt_max_concurrent: int = 20,
    ):
        if answer_trials < 1:
            raise ValueError("answer_trials must be >= 1")
        if qwen_judge_repeats < 1:
            raise ValueError("qwen_judge_repeats must be >= 1")
        if gpt_judge_repeats < 0:
            raise ValueError("gpt_judge_repeats must be >= 0")
        if gpt_judge_repeats and not gpt_judge_client:
            raise ValueError("gpt_judge_repeats > 0 but gpt_judge_client is missing")
        self.judge_client = judge_client
        self.judge_model = judge_model
        self.gpt_judge_client = gpt_judge_client
        self.gpt_judge_model = gpt_judge_model
        self.answer_mode = answer_mode
        self.answer_client = answer_client
        self.answer_model_name = answer_model_name
        self.force_generate_answer = force_generate_answer
        self.judge_temperature = judge_temperature
        self.gpt_judge_temperature = gpt_judge_temperature
        self.answer_trials = int(answer_trials)
        self.qwen_judge_repeats = int(qwen_judge_repeats)
        self.gpt_judge_repeats = int(gpt_judge_repeats)
        self.semaphore = asyncio.Semaphore(max_concurrent)
        self.qwen_request_pool = FairRequestPool(qwen_max_concurrent, "qwen")
        self.gpt_request_pool = FairRequestPool(gpt_max_concurrent, "gpt")
        self.write_lock = asyncio.Lock()
        self.trace_lock = asyncio.Lock()
        self.max_retries = max_retries
        self._trace_entries: Dict[str, Dict[str, Any]] = {}

    def load_processed_keys(self, output_path: str) -> set:
        processed_keys = set()
        if not os.path.exists(output_path):
            return processed_keys

        try:
            with open(output_path, "r", encoding="utf-8") as f:
                for line in f:
                    if not line.strip():
                        continue
                    data = json.loads(line)
                    key = self.get_item_key(data)
                    if key:
                        processed_keys.add(key)
            logger.info(f"从输出文件加载了 {len(processed_keys)} 条已处理记录")
        except Exception as e:
            logger.warning(f"读取已有输出文件时出错: {e}，将从头开始处理")

        return processed_keys

    def get_item_key(self, item: Dict[str, Any]) -> str:
        prompt = item.get("prompt", "")
        identity = item.get("sample_id")
        if identity is None or not str(identity).strip():
            identity = item.get("index", "")
        return f"{identity}|||{prompt}"

    def get_reference_answer(self, item: Dict[str, Any]) -> str:
        outputs = item.get("meta_info").get("references")
        if isinstance(outputs, list) and outputs:
            answer = outputs[0]
            if isinstance(answer, str) and answer.strip():
                return answer.strip()
        raise ValueError("回测模式要求输入数据包含非空 meta_info.references[0]")

    async def generate_candidate_answer(self, item: Dict[str, Any]) -> str:
        if self.answer_mode == "reference":
            return self.get_reference_answer(item)

        if not self.answer_client:
            raise ValueError("自由 LLM 模式下缺少 answer_client")

        question = item.get("prompt")
        if not isinstance(question, str) or not question.strip():
            raise ValueError("缺少有效 prompt，无法生成待评答案")
        sample_key = self.get_item_key(item)
        async with self.qwen_request_pool.request(sample_key):
            return await self.answer_client.generate_answer(question.strip())

    async def generate_candidate_answer_with_retry(self, item: Dict[str, Any]) -> str:
        for attempt in range(self.max_retries + 1):
            try:
                answer = await self.generate_candidate_answer(item)
                if isinstance(answer, str) and answer.strip():
                    return answer.strip()
                raise ValueError("待评答案为空")
            except Exception as e:
                logger.warning(f"生成待评答案失败 (尝试 {attempt + 1}/{self.max_retries + 1}): {str(e)[:200]}")
                if attempt < self.max_retries:
                    await asyncio.sleep(attempt + 1)
                else:
                    raise
        raise RuntimeError("待评答案重试逻辑异常退出")

    async def score_once(
        self,
        score_prompt: str,
        *,
        judge_client: RotatingAPIClient,
        judge_model: str,
        judge_temperature: float,
        request_pool: FairRequestPool,
        sample_key: str,
    ) -> Dict[str, Any]:
        async with request_pool.request(sample_key):
            response = await judge_client.chat_completions_create(
                model=judge_model,
                messages=[{"role": "user", "content": score_prompt}],
                temperature=judge_temperature,
            )
        content = extract_answer(response)
        json_str = extract_json_from_response(content)
        parsed = loads_json_with_repair(json_str)
        if not isinstance(parsed, dict):
            raise ValueError("评分结果必须是 JSON 对象")
        parsed["_raw_response"] = content.strip()
        return parsed

    async def score_with_retry(
        self,
        score_prompt: str,
        *,
        judge_client: Optional[RotatingAPIClient] = None,
        judge_model: Optional[str] = None,
        judge_temperature: Optional[float] = None,
        request_pool: Optional[FairRequestPool] = None,
        sample_key: str = "unknown",
        judge_name: str = "qwen",
    ) -> Dict[str, Any]:
        resolved_client = judge_client or self.judge_client
        resolved_model = judge_model or self.judge_model
        resolved_temperature = self.judge_temperature if judge_temperature is None else judge_temperature
        resolved_pool = request_pool or self.qwen_request_pool
        for attempt in range(self.max_retries + 1):
            try:
                return await self.score_once(
                    score_prompt,
                    judge_client=resolved_client,
                    judge_model=resolved_model,
                    judge_temperature=resolved_temperature,
                    request_pool=resolved_pool,
                    sample_key=sample_key,
                )
            except Exception as e:
                logger.warning(
                    "%s 评分失败 (尝试 %s/%s): %s",
                    judge_name,
                    attempt + 1,
                    self.max_retries + 1,
                    str(e)[:200],
                )
                if attempt < self.max_retries:
                    error_text = str(e)
                    if "调用频率" in error_text or "qpm" in error_text.lower() or "0x04030020" in error_text:
                        await asyncio.sleep(30)
                    else:
                        await asyncio.sleep(attempt + 1)
                else:
                    raise
        raise RuntimeError("评分重试逻辑异常退出")

    async def _register_trace(
        self,
        *,
        sample_key: str,
        trial_index: int,
        judge_name: str,
        repeat_index: int,
        judge_model: str,
        raw_response: str,
    ) -> str:
        trace_source = "|".join([
            EVALUATION_PROTOCOL,
            sample_key,
            str(trial_index),
            judge_name,
            str(repeat_index),
            raw_response,
        ])
        trace_id = hashlib.sha256(trace_source.encode("utf-8")).hexdigest()
        entry = {
            "trace_id": trace_id,
            "evaluation_protocol": EVALUATION_PROTOCOL,
            "sample_key": sample_key,
            "trial_index": trial_index,
            "judge": judge_name,
            "repeat_index": repeat_index,
            "judge_model": judge_model,
            "raw_response": raw_response,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        async with self.trace_lock:
            self._trace_entries[trace_id] = entry
        return trace_id

    async def _normalize_judge_result(
        self,
        *,
        parsed: Dict[str, Any],
        rubric: List[Dict[str, Any]],
        sample_key: str,
        trial_index: int,
        repeat_index: int,
        judge_name: str,
        judge_model: str,
    ) -> Dict[str, Any]:
        normalized_item_scores, total_awarded = normalize_item_scores(
            parsed.get("item_scores", []),
            rubric,
        )
        total_possible = sum(max(0, int(criterion.get("weight", 0) or 0)) for criterion in rubric)
        raw_response = str(parsed.get("_raw_response", "") or "")
        trace_id = await self._register_trace(
            sample_key=sample_key,
            trial_index=trial_index,
            judge_name=judge_name,
            repeat_index=repeat_index,
            judge_model=judge_model,
            raw_response=raw_response,
        )
        return {
            "repeat_index": repeat_index,
            "judge_model": judge_model,
            "item_scores": normalized_item_scores,
            "overall_comment": str(parsed.get("overall_comment", "")).strip(),
            "total_awarded": total_awarded,
            "total_possible": total_possible,
            "score_rate": total_awarded / total_possible if total_possible > 0 else None,
            "raw_response_trace_id": trace_id,
        }

    async def _score_judge_repeat(
        self,
        *,
        final_prompt: str,
        rubric: List[Dict[str, Any]],
        sample_key: str,
        trial_index: int,
        repeat_index: int,
        judge_name: str,
    ) -> Dict[str, Any]:
        is_gpt = judge_name == "gpt"
        client = self.gpt_judge_client if is_gpt else self.judge_client
        model = self.gpt_judge_model if is_gpt else self.judge_model
        temperature = self.gpt_judge_temperature if is_gpt else self.judge_temperature
        pool = self.gpt_request_pool if is_gpt else self.qwen_request_pool
        if client is None:
            raise ValueError(f"{judge_name} judge client is missing")
        parsed = await self.score_with_retry(
            final_prompt,
            judge_client=client,
            judge_model=model,
            judge_temperature=temperature,
            request_pool=pool,
            sample_key=sample_key,
            judge_name=judge_name,
        )
        return await self._normalize_judge_result(
            parsed=parsed,
            rubric=rubric,
            sample_key=sample_key,
            trial_index=trial_index,
            repeat_index=repeat_index,
            judge_name=judge_name,
            judge_model=model,
        )

    async def score_candidate_answer(
        self,
        item: Dict[str, Any],
        candidate_answer: str,
        trial_index: int = 1,
    ) -> Dict[str, Any]:
        score_prompt_template = item.get("score_prompt")
        rubric = item.get("rubric")
        if not isinstance(score_prompt_template, str) or not score_prompt_template.strip():
            raise ValueError("输入数据缺少非空 score_prompt")
        if not isinstance(rubric, list) or not rubric:
            raise ValueError("输入数据缺少非空 rubric")

        final_prompt = build_scoring_prompt(score_prompt_template, candidate_answer.strip())
        sample_key = self.get_item_key(item)
        qwen_tasks = [
            self._score_judge_repeat(
                final_prompt=final_prompt,
                rubric=rubric,
                sample_key=sample_key,
                trial_index=trial_index,
                repeat_index=repeat_index,
                judge_name="qwen",
            )
            for repeat_index in range(1, self.qwen_judge_repeats + 1)
        ]

        async def experimental_gpt(repeat_index: int) -> Dict[str, Any]:
            try:
                return await self._score_judge_repeat(
                    final_prompt=final_prompt,
                    rubric=rubric,
                    sample_key=sample_key,
                    trial_index=trial_index,
                    repeat_index=repeat_index,
                    judge_name="gpt",
                )
            except Exception as exc:
                logger.warning(
                    "GPT 实验评分失败 sample=%s trial=%s repeat=%s error=%s",
                    sample_key,
                    trial_index,
                    repeat_index,
                    str(exc)[:200],
                )
                return {
                    "repeat_index": repeat_index,
                    "judge_model": self.gpt_judge_model,
                    "error": str(exc),
                }

        combined = await asyncio.gather(
            *qwen_tasks,
            *(experimental_gpt(index) for index in range(1, self.gpt_judge_repeats + 1)),
        )
        qwen_results = list(combined[:len(qwen_tasks)])
        gpt_results = list(combined[len(qwen_tasks):])
        qwen_rates = [float(result["score_rate"]) for result in qwen_results]
        gpt_rates = [
            float(result["score_rate"])
            for result in gpt_results
            if result.get("score_rate") is not None and "error" not in result
        ]
        representative_qwen = qwen_results[0]
        trial_result = {
            "trial_index": trial_index,
            "candidate_answer": candidate_answer.strip(),
            "answer_mode": self.answer_mode,
            "answer_model": self.answer_model_name if self.answer_mode == "llm" else "meta_info.references[0]",
            "qwen_judge_results": qwen_results,
            "gpt_judge_results": gpt_results,
            "qwen_score_mean": statistics.fmean(qwen_rates),
            "gpt_score_mean": statistics.fmean(gpt_rates) if gpt_rates else None,
            # Legacy-compatible projection for Round 0 and callers that score one answer.
            "item_scores": deepcopy(representative_qwen["item_scores"]),
            "overall_comment": representative_qwen["overall_comment"],
            "total_awarded": statistics.fmean(qwen_rates) * representative_qwen["total_possible"],
            "total_possible": representative_qwen["total_possible"],
            "judge_model": self.judge_model,
            "judge_raw_response_trace_id": representative_qwen["raw_response_trace_id"],
        }
        return trial_result

    @staticmethod
    def _score_summary(
        rates: List[float],
        *,
        requested_count: int,
        experimental: bool,
    ) -> Dict[str, Any]:
        successful_count = len(rates)
        return {
            "requested_count": requested_count,
            "successful_count": successful_count,
            "failed_count": requested_count - successful_count,
            "score_count": successful_count,
            "score_mean": statistics.fmean(rates) if rates else None,
            "score_min": min(rates) if rates else None,
            "score_max": max(rates) if rates else None,
            "experimental": experimental,
        }

    def aggregate_answer_trials(self, trials: List[Dict[str, Any]]) -> Dict[str, Any]:
        if not trials:
            raise ValueError("至少需要一个 answer trial")
        ordered_trials = sorted(trials, key=lambda trial: int(trial.get("trial_index") or 0))
        qwen_rates = [
            float(result["score_rate"])
            for trial in ordered_trials
            for result in trial.get("qwen_judge_results", [])
        ]
        if len(qwen_rates) != len(ordered_trials) * self.qwen_judge_repeats:
            raise ValueError("必需的 Qwen judge repeat 不完整，拒绝生成在线分数")
        gpt_rates = [
            float(result["score_rate"])
            for trial in ordered_trials
            for result in trial.get("gpt_judge_results", [])
            if "error" not in result and result.get("score_rate") is not None
        ]
        qwen_summary = self._score_summary(
            qwen_rates,
            requested_count=len(ordered_trials) * self.qwen_judge_repeats,
            experimental=False,
        )
        qwen_summary["decision_source"] = "qwen"
        gpt_summary = self._score_summary(
            gpt_rates,
            requested_count=len(ordered_trials) * self.gpt_judge_repeats,
            experimental=True,
        )
        overall_qwen_mean = float(qwen_summary["score_mean"])
        representative = min(
            ordered_trials,
            key=lambda trial: (
                abs(float(trial["qwen_score_mean"]) - overall_qwen_mean),
                int(trial.get("trial_index") or 0),
            ),
        )
        representative_qwen = representative["qwen_judge_results"][0]
        total_possible = int(representative_qwen["total_possible"])
        return {
            "evaluation_protocol": EVALUATION_PROTOCOL,
            "answer_mode": self.answer_mode,
            "answer_model": representative["answer_model"],
            "candidate_answer": representative["candidate_answer"],
            "item_scores": deepcopy(representative_qwen["item_scores"]),
            "overall_comment": representative_qwen["overall_comment"],
            "total_awarded": overall_qwen_mean * total_possible,
            "total_possible": total_possible,
            "judge_model": self.judge_model,
            "judge_raw_response_trace_id": representative_qwen["raw_response_trace_id"],
            "representative_trial_index": representative["trial_index"],
            "answer_trials": ordered_trials,
            "qwen_score_summary": qwen_summary,
            "gpt_score_summary": gpt_summary,
        }

    async def process_item(self, item: Dict[str, Any]) -> Dict[str, Any]:
        async with self.semaphore:
            ensure_sample_identity(item)

            # question evolution 循环中，未进化样本应完全复用上一轮评分结果，
            # 避免重复答题/重评带来的随机波动污染本轮进化效果。
            if item.get("question_evolved") is False:
                scoring_result = item.get("scoring_result")
                if not isinstance(scoring_result, dict) or not scoring_result:
                    raise ValueError("question_evolved=False 但缺少可复用的 scoring_result")
                logger.info(f"透传未进化样本 index={item.get('index')}，不重新答题/评分")
                attach_score_rate(item)
                return item

            existing_answer = None
            if self.answer_mode == "llm" and not self.force_generate_answer:
                raw_existing_answer = item.get("scoring_result", {}).get("candidate_answer")
                if isinstance(raw_existing_answer, str) and raw_existing_answer.strip():
                    existing_answer = raw_existing_answer.strip()
                    logger.info(f"首个 trial 读取已有 candidate_answer (index={item.get('index')})")

            async def run_trial(trial_index: int) -> Dict[str, Any]:
                if self.answer_mode == "reference":
                    candidate_answer = self.get_reference_answer(item)
                elif trial_index == 1 and existing_answer is not None:
                    candidate_answer = existing_answer
                else:
                    candidate_answer = await self.generate_candidate_answer_with_retry(item)
                return await self.score_candidate_answer(item, candidate_answer, trial_index=trial_index)

            trials = await asyncio.gather(*[
                run_trial(trial_index)
                for trial_index in range(1, self.answer_trials + 1)
            ])
            item["scoring_result"] = self.aggregate_answer_trials(list(trials))
            item["evaluation_protocol"] = EVALUATION_PROTOCOL
            item["qwen_score_summary"] = deepcopy(item["scoring_result"]["qwen_score_summary"])
            item["gpt_score_summary"] = deepcopy(item["scoring_result"]["gpt_score_summary"])
            item["representative_trial_index"] = item["scoring_result"]["representative_trial_index"]
            attach_score_rate(item)
            return item

    @staticmethod
    def trace_sidecar_path(output_path: str) -> str:
        return output_path + ".judge_traces.jsonl.gz"

    @staticmethod
    def manifest_path(output_path: str) -> str:
        return output_path + ".manifest.json"

    def load_existing_traces(self, output_path: str) -> None:
        sidecar_path = self.trace_sidecar_path(output_path)
        if not os.path.exists(sidecar_path):
            return
        try:
            with gzip.open(sidecar_path, "rt", encoding="utf-8") as trace_file:
                for line in trace_file:
                    if not line.strip():
                        continue
                    entry = json.loads(line)
                    trace_id = entry.get("trace_id")
                    if isinstance(trace_id, str) and trace_id:
                        self._trace_entries[trace_id] = entry
        except Exception as exc:
            logger.warning("读取已有 judge trace sidecar 失败，将重新写入当前 trace: %s", str(exc)[:200])

    @staticmethod
    def _sha256_file(path: str) -> str:
        digest = hashlib.sha256()
        with open(path, "rb") as source:
            for chunk in iter(lambda: source.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()

    def write_trace_artifacts(self, output_path: str) -> Tuple[str, str]:
        sidecar_path = self.trace_sidecar_path(output_path)
        manifest_path = self.manifest_path(output_path)
        os.makedirs(os.path.dirname(os.path.abspath(sidecar_path)), exist_ok=True)
        sidecar_temp = sidecar_path + ".tmp"
        with gzip.open(sidecar_temp, "wt", encoding="utf-8") as trace_file:
            for trace_id in sorted(self._trace_entries):
                trace_file.write(json.dumps(self._trace_entries[trace_id], ensure_ascii=False) + "\n")
        os.replace(sidecar_temp, sidecar_path)

        manifest = {
            "evaluation_protocol": EVALUATION_PROTOCOL,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "scoring_artifact": {
                "path": os.path.basename(output_path),
                "sha256": self._sha256_file(output_path) if os.path.exists(output_path) else None,
            },
            "judge_trace_sidecar": {
                "path": os.path.basename(sidecar_path),
                "compression": "gzip",
                "record_count": len(self._trace_entries),
                "sha256": self._sha256_file(sidecar_path),
            },
        }
        manifest_temp = manifest_path + ".tmp"
        with open(manifest_temp, "w", encoding="utf-8") as manifest_file:
            json.dump(manifest, manifest_file, ensure_ascii=False, indent=2)
            manifest_file.write("\n")
        os.replace(manifest_temp, manifest_path)
        return sidecar_path, manifest_path

    def _print_scoring_stats(self, results: List[Dict[str, Any]]):
        """自动统计并打印得分率。"""
        if not results:
            return

        stats = []
        total_score = 0
        total_possible = 0

        for item in results:
            idx = item.get("index", "N/A")
            sr = item.get("scoring_result", {})
            item_scores = sr.get("item_scores", [])
            awarded = sr.get("total_awarded", 0)

            # 判断是否含负分项：按正项权重之和作为满分
            has_negative = any(it.get("weight", 0) < 0 for it in item_scores)
            if has_negative:
                possible = sum(it.get("weight", 0) for it in item_scores if it.get("weight", 0) > 0)
            else:
                possible = sr.get("total_possible", 0)

            rate = awarded / possible if possible > 0 else 0
            stats.append({
                "index": idx,
                "awarded": awarded,
                "possible": possible,
                "rate": rate,
                "has_negative": has_negative
            })
            total_score += awarded
            total_possible += possible

        overall_rate = total_score / total_possible if total_possible > 0 else 0

        print("\n" + "=" * 60)
        print("评分统计结果")
        print("=" * 60)
        print(f"总样本数: {len(stats)}")
        print(f"总体平均得分率: {overall_rate:.2%} ({total_score}/{total_possible})")
        print("-" * 60)
        print(f"{'Index':>8s} {'得分':>10s} {'满分':>10s} {'得分率':>10s} {'负分项':>8s}")
        print("-" * 60)
        # 按 index 从小到大排序，最多打印前10个
        sorted_stats = sorted(stats, key=lambda x: x["index"])
        for s in sorted_stats[:10]:
            neg_flag = "是" if s["has_negative"] else "否"
            print(f"{s['index']:>8} {s['awarded']:>10} {s['possible']:>10} {s['rate']:>9.2%} {neg_flag:>8}")
        if len(sorted_stats) > 10:
            print(f"{'...':>8} ({len(sorted_stats) - 10} 条已省略)")
        print("=" * 60 + "\n")

    async def process_file(self, input_path: str, output_path: str):
        if not os.path.exists(input_path):
            raise FileNotFoundError(f"输入文件不存在: {input_path}")

        items = []
        with open(input_path, "r", encoding="utf-8") as f:
            content = f.read().strip()
            if content.startswith("["):
                # JSON array format
                items = json.loads(content)
            else:
                # JSONL format
                for line in content.splitlines():
                    if line.strip():
                        items.append(json.loads(line))

        processed_keys = self.load_processed_keys(output_path)
        self.load_existing_traces(output_path)
        original_count = len(items)
        items = [item for item in items if self.get_item_key(item) not in processed_keys]
        skipped_count = original_count - len(items)
        if skipped_count > 0:
            logger.info(f"跳过 {skipped_count} 条已处理数据")

        if not items:
            logger.info("所有数据已处理完成，无需继续")
            if os.path.exists(output_path):
                self.write_trace_artifacts(output_path)
            return

        logger.info(f"开始评分 {len(items)} 条数据，并发限制 {self.semaphore._value}")

        os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
        failed_path = output_path + ".failed"
        file_mode = "a" if processed_keys else "w"
        results: List[Dict[str, Any]] = []
        failed_count = 0

        async def run_one(item: Dict[str, Any], out_f, fail_f):
            nonlocal failed_count
            try:
                processed_item = await self.process_item(item)
                async with self.write_lock:
                    out_f.write(json.dumps(processed_item, ensure_ascii=False) + "\n")
                    out_f.flush()
                    results.append(processed_item)
            except Exception as e:
                failed_item = dict(item)
                failed_item["scoring_error"] = str(e)
                logger.error(f"评分失败 index={item.get('index')} prompt={str(item.get('prompt', ''))[:80]} error={e}")
                async with self.write_lock:
                    fail_f.write(json.dumps(failed_item, ensure_ascii=False) + "\n")
                    fail_f.flush()
                    failed_count += 1

        with open(output_path, file_mode, encoding="utf-8") as out_f, \
             open(failed_path, file_mode, encoding="utf-8") as fail_f:
            tasks = [run_one(item, out_f, fail_f) for item in items]
            try:
                from tqdm.asyncio import tqdm
                await tqdm.gather(*tasks)
            except ImportError:
                await asyncio.gather(*tasks)

        self._print_scoring_stats(results)

        logger.info(f"评分完成，结果保存至: {output_path}")
        sidecar_path, manifest_path = self.write_trace_artifacts(output_path)
        logger.info("judge trace sidecar: %s", sidecar_path)
        logger.info("scoring manifest: %s", manifest_path)
        if os.path.exists(failed_path) and os.path.getsize(failed_path) == 0:
            os.remove(failed_path)
        elif os.path.exists(failed_path):
            logger.warning(f"存在失败数据，已保存至: {failed_path}")

        if failed_count:
            raise RuntimeError(
                f"scoring 阶段有 {failed_count}/{len(items)} 条记录失败；"
                f"失败详情见 {failed_path}，已停止后续流水线。"
            )


async def main():
    parser = argparse.ArgumentParser(description="基于 gen_rubric.py 产出的 score_prompt 对答案进行自动评分")
    parser.add_argument("--input", type=str, required=True, help="gen_rubric.py 输出的 jsonl 文件路径")
    parser.add_argument("--output", type=str, help="输出 jsonl 文件路径，默认在输入文件名后追加 _scored")
    parser.add_argument("--concurrency", type=int, default=50, help="并行处理的题目 worker 数量")
    parser.add_argument("--retries", type=int, default=3, help="评分调用失败时的重试次数")
    parser.add_argument("--answer-trials", type=int, default=None, help="每题回答 trial 数；llm 模式默认 3，reference 模式默认 1")
    parser.add_argument("--qwen-judge-repeats", type=int, default=DEFAULT_QWEN_JUDGE_REPEATS, help="每个回答的 Qwen judge 独立评分次数")
    parser.add_argument("--gpt-judge-repeats", type=int, default=DEFAULT_GPT_JUDGE_REPEATS, help="每个回答的 GPT 实验复评次数；设为 0 可关闭")
    parser.add_argument("--qwen-max-concurrent", type=int, default=20, help="Qwen answer 与 Qwen judge 共享请求池的在途上限")
    parser.add_argument("--gpt-max-concurrent", type=int, default=20, help="GPT judge 独立请求池的在途上限")
    parser.add_argument("--judge-model", type=str, default=JUDGE_MODEL, help="评分模型名称")
    parser.add_argument("--judge-base-url", type=str, default=JUDGE_BASE_URL, help="评分模型 OpenAI-compatible base_url")
    parser.add_argument("--judge-api-key", action="append", default=None, help="评分模型 API key；可多次传入。本地 Qwen 服务不需要 key 时可不传。")
    parser.add_argument("--judge-temperature", type=float, default=0.0, help="评分模型 temperature，默认 0.0")
    parser.add_argument("--gpt-judge-model", type=str, default=GPT_JUDGE_MODEL, help="GPT 实验评分模型名称")
    parser.add_argument("--gpt-judge-base-url", type=str, default=GPT_JUDGE_BASE_URL, help="GPT 实验评分服务 base_url")
    parser.add_argument("--gpt-judge-api-key", action="append", default=None, help="GPT 实验评分 API key；可多次传入")
    parser.add_argument("--gpt-judge-temperature", type=float, default=0.0, help="GPT 实验评分 temperature")
    parser.add_argument(
        "--answer-mode",
        type=str,
        choices=["reference", "llm"],
        default="reference",
        help="待评答案来源：reference=直接使用 meta_info.references[0]；llm=读取已有 candidate_answer 或调用自由配置模型生成答案"
    )
    parser.add_argument("--answer-base-url", type=str, default=ANSWER_BASE_URL, help="待评答案模型的 base_url")
    parser.add_argument("--answer-api-key", type=str, default="", help="待评答案模型的 api_key；本地 Qwen 服务不需要 key 时可为空字符串")
    parser.add_argument("--answer-model", type=str, default=ANSWER_MODEL, help="待评答案模型名称")
    parser.add_argument(
        "--force-generate-answer",
        "--ignore-existing-answer",
        dest="force_generate_answer",
        action="store_true",
        help="answer-mode=llm 时忽略已有 scoring_result.candidate_answer，强制重新生成待评答案",
    )
    args = parser.parse_args()

    resolved_answer_trials = args.answer_trials
    if resolved_answer_trials is None:
        resolved_answer_trials = DEFAULT_ANSWER_TRIALS if args.answer_mode == "llm" else 1
    if resolved_answer_trials < 1:
        raise ValueError("--answer-trials 必须 >= 1")
    if args.qwen_judge_repeats < 1:
        raise ValueError("--qwen-judge-repeats 必须 >= 1")
    if args.gpt_judge_repeats < 0:
        raise ValueError("--gpt-judge-repeats 必须 >= 0")
    if args.gpt_judge_repeats and not (args.gpt_judge_base_url or "").strip():
        raise ValueError("启用 GPT judge 时必须提供 --gpt-judge-base-url")
    if args.gpt_judge_repeats and not (args.gpt_judge_model or "").strip():
        raise ValueError("启用 GPT judge 时必须提供 --gpt-judge-model")

    if not args.output:
        base, ext = os.path.splitext(args.input)
        args.output = f"{base}_scored{ext}"

    answer_client = None
    answer_model_name = ""
    if args.answer_mode == "llm":
        resolved_answer_base_url = (args.answer_base_url or ANSWER_BASE_URL).strip()
        resolved_answer_model = (args.answer_model or ANSWER_MODEL).strip()
        if not resolved_answer_base_url:
            raise ValueError("自由 LLM 模式下必须提供 --answer-base-url")
        if not resolved_answer_model:
            raise ValueError("自由 LLM 模式下必须提供 --answer-model")
        answer_client = AnswerLLMClient(
            base_url=resolved_answer_base_url,
            api_key=resolve_answer_api_key(args.answer_api_key),
            model=resolved_answer_model
        )
        answer_model_name = resolved_answer_model

    judge_client = RotatingAPIClient(
        base_url=args.judge_base_url or JUDGE_BASE_URL,
        api_keys=parse_api_keys(args.judge_api_key)
    )
    gpt_judge_client = None
    if args.gpt_judge_repeats:
        gpt_judge_client = RotatingAPIClient(
            base_url=args.gpt_judge_base_url,
            api_keys=parse_gpt_judge_api_keys(args.gpt_judge_api_key),
        )

    processor = ScoringProcessor(
        judge_client=judge_client,
        judge_model=args.judge_model or JUDGE_MODEL,
        answer_mode=args.answer_mode,
        max_concurrent=args.concurrency,
        max_retries=args.retries,
        answer_client=answer_client,
        answer_model_name=answer_model_name,
        force_generate_answer=args.force_generate_answer,
        judge_temperature=args.judge_temperature,
        gpt_judge_client=gpt_judge_client,
        gpt_judge_model=args.gpt_judge_model,
        gpt_judge_temperature=args.gpt_judge_temperature,
        answer_trials=resolved_answer_trials,
        qwen_judge_repeats=args.qwen_judge_repeats,
        gpt_judge_repeats=args.gpt_judge_repeats,
        qwen_max_concurrent=args.qwen_max_concurrent,
        gpt_max_concurrent=args.gpt_max_concurrent,
    )

    await processor.process_file(args.input, args.output)


if __name__ == "__main__":
    asyncio.run(main())
