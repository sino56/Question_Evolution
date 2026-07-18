import json
import asyncio
import logging
import os
import argparse
import re
import random
import unicodedata
from collections import Counter
from typing import List, Dict, Any, Tuple
from openai import AsyncOpenAI

import sys
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from local_api_config import get_config_list, get_config_value
from pipeline_runtime import (
    AtomicJsonlStageWriter,
    FairRequestPool,
    StageMetrics,
    append_performance_event,
    bounded_async_map,
    ensure_passthrough_reusable,
    iter_json_records,
    stable_record_key,
    validate_published_artifact,
)

QA_MODEL = (
    os.getenv("ANSWER_MODEL")
    or os.getenv("GPT_MODEL")
    or get_config_value("ANSWER_MODEL", "QA_MODEL", "GPT_MODEL", default="gpt-5.4")
)
BASE_URL = (
    os.getenv("ANSWER_BASE_URL")
    or os.getenv("OPENAI_BASE_URL")
    or get_config_value("ANSWER_BASE_URL", "BASE_URL", "OPENAI_BASE_URL", default="")
)


def parse_api_keys(cli_keys: List[str] = None) -> List[str]:
    if cli_keys:
        keys = [key.strip() for key in cli_keys if key and key.strip()]
        if keys:
            return keys
    raw = (
        os.getenv("ANSWER_API_KEYS")
        or os.getenv("GPT_API_KEYS")
        or os.getenv("OPENAI_API_KEYS")
        or os.getenv("OPENAI_API_KEY")
        or ""
    )
    keys = [part.strip() for part in raw.split(",") if part.strip()]
    if keys:
        return keys
    return get_config_list(
        "ANSWER_API_KEYS",
        "GPT_API_KEYS",
        "HIAPI_KEYS_BIG",
        "OPENAI_API_KEYS",
        "OPENAI_API_KEY",
        "API_KEYS",
    )

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

MAX_ANSWER_LENGTH = 12000
MIN_ANSWER_LENGTH = 50
MAX_CONSECUTIVE_SAME_CHAR = 20
MIN_REPEATED_LINE_OCCURRENCES = 4
REPEATED_LINE_MIN_LENGTH = 12
MAX_REPEAT_LINE_RATIO = 0.6
MAX_PUNCTUATION_RATIO = 0.45
MAX_NON_TEXT_RATIO = 0.35
MAX_TOP_CHAR_RATIO = 0.35
MAX_DUPLICATE_SENTENCE_RATIO = 0.6
REQUEST_TIMEOUT_SECONDS = 180.0
EMPTY_RETRY_BASE_SLEEP_SECONDS = 5.0
EMPTY_RETRY_MAX_SLEEP_SECONDS = 60.0
REFUSAL_PATTERNS = [
    "我无法",
    "我不能",
    "不能提供",
    "无法提供",
    "无法回答",
    "不能回答",
    "无法协助",
    "不能协助",
    "无法帮助",
    "不能帮助",
    "不便提供",
    "不能为你提供",
    "无法为你提供",
    "抱歉，我不能",
    "抱歉，我无法",
    "对不起，我不能",
    "对不起，我无法",
]

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

class AnswerCollector:
    def __init__(
        self,
        api_keys: List[str],
        base_url: str,
        model: str,
        max_concurrent: int = 20,
        max_retries: int = 3,
        script_gt_guide: bool = False,
        request_timeout: float = REQUEST_TIMEOUT_SECONDS,
        empty_retry_base_sleep: float = EMPTY_RETRY_BASE_SLEEP_SECONDS,
        request_concurrent: int = None,
    ):
        self.api_keys = api_keys
        self.current_key_index = 0
        self.base_url = base_url
        self.model = model
        request_limit = max_concurrent if request_concurrent is None else request_concurrent
        self.request_pool = FairRequestPool(max(1, request_limit), "reference_answer")
        # Kept as a compatibility attribute for callers that inspect it.
        self.semaphore = asyncio.Semaphore(max(1, request_limit))
        self.write_lock = asyncio.Lock()
        self.max_retries = max_retries
        self.key_lock = asyncio.Lock()  # 用于保护 key 切换的锁
        self.script_gt_guide = script_gt_guide
        self.request_timeout = request_timeout
        self.empty_retry_base_sleep = empty_retry_base_sleep
        
        # 初始化第一个 client
        self.client = AsyncOpenAI(
            api_key=self.api_keys[self.current_key_index],
            base_url=self.base_url,
            timeout=self.request_timeout,
        )
    
    async def _switch_to_next_key(self):
        """切换到下一个 API key"""
        self.current_key_index = (self.current_key_index + 1) % len(self.api_keys)
        old_key_prefix = self.api_keys[self.current_key_index - 1][:10] + "***" if self.current_key_index > 0 else self.api_keys[0][:10] + "***"
        new_key_prefix = self.api_keys[self.current_key_index][:10] + "***"
        logger.warning(f"API Key 额度用尽，切换到下一个 key: {old_key_prefix} -> {new_key_prefix}")
        old_client = self.client
        self.client = AsyncOpenAI(
            api_key=self.api_keys[self.current_key_index],
            base_url=self.base_url,
            timeout=self.request_timeout,
        )
        await old_client.close()

    async def close(self):
        close = getattr(self.client, "close", None)
        if close is not None:
            result = close()
            if asyncio.iscoroutine(result):
                await result
    
    def _is_token_exhausted_error(self, error: Exception) -> bool:
        """检查错误是否是 token 额度用尽的错误"""
        error_str = str(error)
        return (
            "401" in error_str and 
            ("TokenStatusExhausted" in error_str or "令牌额度已用尽" in error_str)
        )

    def _contains_garbled_text(self, text: str) -> bool:
        if "\ufffd" in text:
            return True

        invalid_controls = 0
        non_text_chars = 0
        punctuation_chars = 0
        visible_chars = 0

        for ch in text:
            if ch.isspace():
                continue
            visible_chars += 1
            category = unicodedata.category(ch)
            if category.startswith("C") and ch not in "\n\r\t":
                invalid_controls += 1
            if category.startswith("P") or category.startswith("S"):
                punctuation_chars += 1
            if not (
                "\u4e00" <= ch <= "\u9fff"
                or ch.isascii() and ch.isalnum()
                or ch in "，。！？；：、（）《》“”‘’\"'()[]{}<>-_/+*=：,.!?;%\n\r\t "
            ):
                non_text_chars += 1

        if visible_chars == 0:
            return True
        if invalid_controls > 0:
            return True
        if punctuation_chars / visible_chars > MAX_PUNCTUATION_RATIO:
            return True
        if non_text_chars / visible_chars > MAX_NON_TEXT_RATIO:
            return True
        return False

    def _has_excessive_repetition(self, text: str) -> bool:
        normalized = re.sub(r"\s+", " ", text).strip()
        if not normalized:
            return False

        if re.search(r"(.)\1{%d,}" % (MAX_CONSECUTIVE_SAME_CHAR - 1), normalized):
            return True

        char_counter = Counter(ch for ch in normalized if not ch.isspace())
        total_chars = sum(char_counter.values())
        if total_chars > 0:
            most_common_ratio = char_counter.most_common(1)[0][1] / total_chars
            if most_common_ratio > MAX_TOP_CHAR_RATIO:
                return True

        lines = [line.strip() for line in text.splitlines() if line.strip()]
        if len(lines) >= MIN_REPEATED_LINE_OCCURRENCES:
            line_counter = Counter(line for line in lines if len(line) >= REPEATED_LINE_MIN_LENGTH)
            repeated_line_count = sum(
                count for count in line_counter.values()
                if count >= MIN_REPEATED_LINE_OCCURRENCES
            )
            if repeated_line_count and repeated_line_count / len(lines) > MAX_REPEAT_LINE_RATIO:
                return True

        sentence_candidates = re.split(r"[。！？!?；;\n]+", normalized)
        sentences = [sentence.strip() for sentence in sentence_candidates if len(sentence.strip()) >= 8]
        if len(sentences) >= 4:
            sentence_counter = Counter(sentences)
            duplicate_sentences = sum(count for count in sentence_counter.values() if count >= 3)
            if duplicate_sentences / len(sentences) > MAX_DUPLICATE_SENTENCE_RATIO:
                return True

        return False

    def detect_refusal_answer(self, answer: str) -> Tuple[bool, str]:
        """检测模型是否拒答。"""
        text = re.sub(r"\s+", "", (answer or ""))
        if not text:
            return False, ""

        for pattern in REFUSAL_PATTERNS:
            if pattern in text:
                return True, pattern

        return False, ""

    def validate_answer_quality(self, answer: str) -> Tuple[bool, str]:
        """检测答案质量，过滤乱码、重复刷屏、异常过长等情况。"""
        if not answer:
            return False, "empty_answer"

        text = answer.strip()
        if not text:
            return False, "empty_answer"

        if text.startswith("Error:"):
            return False, "error_answer"

        is_refusal, refusal_pattern = self.detect_refusal_answer(text)
        if is_refusal:
            return False, f"refusal_answer:{refusal_pattern}"

        if len(text) < MIN_ANSWER_LENGTH:
            return False, f"answer_too_short:{len(text)}"

        if len(text) > MAX_ANSWER_LENGTH:
            return False, f"answer_too_long:{len(text)}"

        if self._contains_garbled_text(text):
            return False, "garbled_text"

        if self._has_excessive_repetition(text):
            return False, "excessive_repetition"

        return True, ""

    def _retry_sleep_seconds(self, attempt: int, reason: str) -> float:
        """空回复通常来自上游限流/过载，需要比普通异常更长的退避。"""
        if reason == "empty_answer":
            base = max(0.0, self.empty_retry_base_sleep)
            delay = min(EMPTY_RETRY_MAX_SLEEP_SECONDS, base * (2 ** attempt))
        else:
            delay = attempt + 1
        return delay + random.uniform(0, 1.0)

    async def call_llm_raw(self, question: str, sample_key: str = "unknown") -> str:
        """原生调用 LLM，没有任何额外的 instruction"""
        async with self.request_pool.request(sample_key):
            response = await self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "user", "content": question}
                ],
                # temperature=0.1, # 采样时通常需要一定的随机性
            )
            # content = response.choices[0].message.content
            content = extract_answer(response)
            return content.strip() if content else ""

    async def call_llm_with_retry(self, question: str, sample_key: str = "unknown") -> str:
        """带重试机制的 LLM 调用，支持 key 切换"""
        key_switches = 0  # 记录 key 切换次数
        max_key_switches = len(self.api_keys)  # 最多切换次数
        
        while key_switches < max_key_switches:
            for attempt in range(self.max_retries + 1):
                try:
                    answer = await self.call_llm_raw(question, sample_key)
                    is_valid, reason = self.validate_answer_quality(answer)
                    if is_valid:
                        return answer
                    logger.warning(f"第 {attempt + 1} 次尝试返回异常答案({reason})，重试中...")
                except Exception as e:
                    # 检查是否是 token 额度用尽的错误
                    if self._is_token_exhausted_error(e):
                        async with self.key_lock:
                            # 再次检查，避免多个协程同时切换
                            if self._is_token_exhausted_error(e):
                                await self._switch_to_next_key()
                                key_switches += 1
                                break  # 跳出内层循环，使用新 key 重试
                    logger.error(f"第 {attempt + 1} 次尝试失败: {e}")
                    reason = "exception"
                
                if attempt < self.max_retries:
                    await asyncio.sleep(self._retry_sleep_seconds(attempt, reason))
            else:
                # 内层循环正常结束（没有 break），说明重试次数用完
                # 如果已经切换过所有 key，则返回空
                if key_switches >= max_key_switches - 1:
                    return ""
                # 否则继续尝试下一个 key
                async with self.key_lock:
                    await self._switch_to_next_key()
                    key_switches += 1
        
        return ""

    async def process_item(self, item: Dict[str, Any], num_samples: int) -> Dict[str, Any]:
        """为单个问题采集多次回答"""
        question = item.get("prompt")
        if not question:
            item["answer_extra"] = []
            item["answer_quality_issues"] = []
            return item

        # 如果开启了 script_gt_guide，在原问题前拼接 ground truth 指引
        if self.script_gt_guide:
            meta_info = item.get("meta_info", {})
            ground_truth = meta_info.get("ground_truth", "")
            annotation = meta_info.get("annotation", "")
            guide_prefix = (
                "你将为下面这道题生成标准答案，为了校准标答的正确性，以下将告知你标答提示：\n"
                f"监控日志异常属性：{ground_truth}\n"
                f"监控日志标注：{annotation}\n\n"
                "你在生成标答时不允许提及“已知指引”“标答提示”“标准答案方向”“提前知道”等"
                "暴露你提前知道了标准答案方向的字眼，要假装不知道指引。\n\n"
                f"{question}"
            )
            question = guide_prefix

        sample_key = stable_record_key(item)
        tasks = [self.call_llm_with_retry(question, sample_key) for _ in range(num_samples)]
        answers = await asyncio.gather(*tasks)

        item["answer_extra"] = answers
        item["answer_quality_issues"] = []
        for sample_index, answer in enumerate(answers):
            is_valid, reason = self.validate_answer_quality(answer)
            if not is_valid:
                item["answer_quality_issues"].append({
                    "sample_index": sample_index,
                    "reason": reason,
                    "preview": (answer or "")[:200]
                })
        return item

    def load_processed_prompts(self, output_path: str) -> set:
        """从已存在的输出文件中加载已处理的问题"""
        processed_prompts = set()
        if os.path.exists(output_path):
            try:
                with open(output_path, 'r', encoding='utf-8') as f:
                    for line in f:
                        if line.strip():
                            data = json.loads(line)
                            prompt = data.get("prompt")
                            if prompt:
                                processed_prompts.add(prompt)
                logger.info(f"从输出文件加载了 {len(processed_prompts)} 条已处理的记录")
            except Exception as e:
                logger.warning(f"读取已有输出文件时出错: {e}，将从头开始处理")
        return processed_prompts

    async def process_file(
        self,
        input_path: str,
        output_path: str,
        num_samples: int,
        max_concurrent_items: int,
        *,
        performance_path: str = None,
        config: Dict[str, Any] = None,
    ):
        """Stream records through bounded workers and publish atomically."""
        if not os.path.exists(input_path):
            raise FileNotFoundError(f"输入文件不存在: {input_path}")

        stage = "collect_answers"
        resolved_config = config or {
            "model": getattr(self, "model", "unknown"),
            "samples": num_samples,
            "item_concurrency": max_concurrent_items,
        }
        valid, _ = validate_published_artifact(
            output_path,
            stage=stage,
            input_path=input_path,
            config=resolved_config,
        )
        if valid:
            logger.info("Verified published answer artifact; skipping %s", output_path)
            return

        metrics = StageMetrics(stage)
        metrics.input_bytes = os.path.getsize(input_path)
        writer = AtomicJsonlStageWriter(
            output_path,
            stage=stage,
            input_path=input_path,
            config=resolved_config,
            code_paths=[__file__],
            metrics=metrics,
        )
        failed_path = output_path + ".failed"
        failed_count = 0

        async def worker(item: Dict[str, Any]):
            item_index = item.get("index", item.get("sample_id", "unknown"))
            try:
                if item.get("question_evolved") is False:
                    ensure_passthrough_reusable(item, stage=stage)
                    return dict(item), None

                processed_item = await self.process_item(item, num_samples)
                answers = processed_item.get("answer_extra", [])
                answer_quality_issues = processed_item.get("answer_quality_issues", [])
                is_valid = (
                    len(answer_quality_issues) == 0
                    and len(answers) == num_samples
                    and all(isinstance(answer, str) and answer.strip() for answer in answers)
                )
                meta_info = processed_item.get("meta_info")
                meta_info = dict(meta_info) if isinstance(meta_info, dict) else {}
                meta_info["references"] = answers
                transformed = dict(processed_item)
                transformed["index"] = item.get("index", item_index)
                transformed["prompt"] = processed_item.get("prompt")
                transformed["meta_info"] = meta_info
                transformed.pop("answer_extra", None)
                transformed.pop("answer_quality_issues", None)
                if not is_valid:
                    transformed["quality_issues"] = answer_quality_issues
                    return None, transformed
                return transformed, None
            except Exception as exc:
                failed = dict(item)
                failed["answer_collection_error"] = str(exc)
                return None, failed

        async def on_result(_sequence: int, item: Dict[str, Any], outcome) -> None:
            nonlocal failed_count
            result, failed = outcome
            if result is not None:
                writer.add_group(stable_record_key(item), [result])
            if failed is not None:
                os.makedirs(os.path.dirname(os.path.abspath(failed_path)), exist_ok=True)
                with open(failed_path, "a", encoding="utf-8") as target:
                    target.write(json.dumps(failed, ensure_ascii=False) + "\n")
                    target.flush()
                failed_count += 1

        def pending_records():
            for item in iter_json_records(input_path, stage=stage):
                if stable_record_key(item) in writer.processed_keys:
                    continue
                yield item

        try:
            await bounded_async_map(
                pending_records(),
                worker,
                concurrency=max(1, max_concurrent_items),
                on_result=on_result,
                metrics=metrics,
                ordered_results=True,
            )
            request_pool = getattr(self, "request_pool", None)
            if request_pool is not None:
                metrics.request_pool_peaks[request_pool.name] = request_pool.peak_active
            if failed_count:
                raise RuntimeError(
                    f"answer collection 阶段有 {failed_count}/{metrics.input_records} 条记录失败；"
                    f"失败详情见 {failed_path}，已停止后续流水线。"
                )
            writer.publish()
        except Exception:
            writer.close()
            append_performance_event(performance_path, metrics.event(status="failed"))
            raise

        if os.path.exists(failed_path) and os.path.getsize(failed_path) == 0:
            os.remove(failed_path)
        append_performance_event(performance_path, metrics.event())
        logger.info("处理完成，结果保存至: %s", output_path)

async def main():
    parser = argparse.ArgumentParser(description="采集 LLM 对问题的原生回答")
    parser.add_argument("--input", type=str, required=True, help="输入的 jsonl 文件路径")
    parser.add_argument("--output", type=str, help="输出的 jsonl 文件路径 (默认在输入文件名后加 _with_answers)")
    parser.add_argument("--samples", type=int, default=1, help="每个问题的采样次数")
    parser.add_argument("--concurrency", type=int, default=20, help="并行处理的问题数量")
    parser.add_argument(
        "--request-concurrency",
        type=int,
        default=20,
        help="参考答案服务真实在途请求上限",
    )
    parser.add_argument("--model", type=str, default=QA_MODEL, help="使用的模型名称")
    parser.add_argument("--base-url", type=str, default=BASE_URL, help="OpenAI-compatible base_url")
    parser.add_argument("--api-key", action="append", default=None, help="API key；可多次传入，默认读取 ANSWER_API_KEYS/GPT_API_KEYS/OPENAI_API_KEY")
    parser.add_argument("--retries", type=int, default=6, help="LLM 调用失败或返回空答案时的重试次数")
    parser.add_argument("--request-timeout", type=float, default=REQUEST_TIMEOUT_SECONDS, help="单次请求 timeout 秒数")
    parser.add_argument(
        "--empty-retry-base-sleep",
        type=float,
        default=EMPTY_RETRY_BASE_SLEEP_SECONDS,
        help="空答案重试的基础退避秒数；实际按指数退避并加少量随机抖动",
    )
    parser.add_argument("--script_gt_guide", action="store_true", help="开启时，在原问题前拼接 ground truth 指引以校准标答正确性")
    parser.add_argument(
        "--performance-events",
        default=None,
        help="Append metrics to this performance_events.jsonl file.",
    )

    args = parser.parse_args()

    if not args.output:
        base, ext = os.path.splitext(args.input)
        args.output = f"{base}_with_answers{ext}"

    api_keys = parse_api_keys(args.api_key)
    if not api_keys:
        raise ValueError("缺少 ANSWER_API_KEYS/GPT_API_KEYS/OPENAI_API_KEY 或 --api-key")
    
    collector = AnswerCollector(
        api_keys=api_keys,
        base_url=args.base_url or BASE_URL,
        model=args.model or QA_MODEL,
        max_concurrent=args.request_concurrency,
        max_retries=args.retries,
        script_gt_guide=args.script_gt_guide,
        request_timeout=args.request_timeout,
        empty_retry_base_sleep=args.empty_retry_base_sleep,
    )

    try:
        await collector.process_file(
            input_path=args.input,
            output_path=args.output,
            num_samples=args.samples,
            max_concurrent_items=args.concurrency,
            performance_path=args.performance_events,
            config={
                "model": args.model or QA_MODEL,
                "base_url": args.base_url or BASE_URL,
                "samples": args.samples,
                "item_concurrency": args.concurrency,
                "request_concurrency": args.request_concurrency,
                "retries": args.retries,
                "request_timeout": args.request_timeout,
                "script_gt_guide": args.script_gt_guide,
            },
        )
    finally:
        await collector.close()

if __name__ == "__main__":
    asyncio.run(main())
