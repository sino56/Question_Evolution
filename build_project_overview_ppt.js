const pptxgen = require('pptxgenjs');

const pptx = new pptxgen();
pptx.layout = 'LAYOUT_WIDE';
pptx.author = 'OpenAI Codex';
pptx.company = 'Question Evolution Pipeline';
pptx.subject = 'Question Evolution Pipeline 项目全景分析';
pptx.title = 'Question Evolution Pipeline｜项目全景分析';
pptx.lang = 'zh-CN';
pptx.theme = {
  headFontFace: 'Microsoft YaHei',
  bodyFontFace: 'Microsoft YaHei',
  lang: 'zh-CN',
};
pptx.defineLayout({ name: 'CUSTOM_WIDE', width: 13.333, height: 7.5 });
pptx.layout = 'CUSTOM_WIDE';

const C = {
  navy: '102A43',
  blue: '2979B8',
  teal: '168F86',
  mint: 'E7F5F1',
  sky: 'EAF3FA',
  coral: 'E86A5B',
  amber: 'E2A83B',
  yellow: 'FFF5D8',
  slate: '486581',
  muted: '829AB1',
  ink: '243B53',
  light: 'F5F7FA',
  line: 'D9E2EC',
  white: 'FFFFFF',
  green: '2F855A',
  redBg: 'FFF0EE',
};

const W = 13.333;
const H = 7.5;
const FONT = 'Microsoft YaHei';

function addBg(slide, color = C.white) {
  slide.background = { color };
}

function addHeader(slide, section, title, subtitle = '') {
  slide.addText(section.toUpperCase(), {
    x: 0.58, y: 0.33, w: 3.2, h: 0.22,
    fontFace: FONT, fontSize: 8.5, color: C.teal, bold: true,
    charSpacing: 1.1, margin: 0,
  });
  slide.addText(title, {
    x: 0.58, y: 0.62, w: 12.0, h: 0.5,
    fontFace: FONT, fontSize: 26, color: C.navy, bold: true,
    margin: 0, breakLine: false,
  });
  if (subtitle) {
    slide.addText(subtitle, {
      x: 0.6, y: 1.17, w: 11.7, h: 0.28,
      fontFace: FONT, fontSize: 10.5, color: C.slate, margin: 0,
    });
  }
  slide.addShape(pptx.ShapeType.line, {
    x: 0.58, y: 1.54, w: 12.15, h: 0,
    line: { color: C.line, width: 0.8 },
  });
}

function addFooter(slide, num) {
  slide.addShape(pptx.ShapeType.line, {
    x: 0.58, y: 7.08, w: 12.15, h: 0,
    line: { color: C.line, width: 0.65 },
  });
  slide.addText('QUESTION EVOLUTION PIPELINE  ·  项目全景分析', {
    x: 0.58, y: 7.16, w: 5.2, h: 0.16,
    fontFace: FONT, fontSize: 7.2, color: C.muted, margin: 0,
  });
  slide.addText(String(num).padStart(2, '0'), {
    x: 12.25, y: 7.13, w: 0.45, h: 0.2,
    fontFace: FONT, fontSize: 8, color: C.teal, bold: true, align: 'right', margin: 0,
  });
}

function addCard(slide, x, y, w, h, opts = {}) {
  const {
    fill = C.white,
    line = C.line,
    radius = 0.08,
    shadow = false,
  } = opts;
  slide.addShape(pptx.ShapeType.roundRect, {
    x, y, w, h,
    rectRadius: radius,
    fill: { color: fill },
    line: { color: line, width: 0.8 },
    shadow: shadow ? { type: 'outer', color: 'B8C6D1', opacity: 0.15, blur: 1, angle: 45, distance: 1 } : undefined,
  });
}

function addPill(slide, text, x, y, w, color = C.teal, fill = C.mint) {
  slide.addShape(pptx.ShapeType.roundRect, {
    x, y, w, h: 0.27,
    rectRadius: 0.08,
    fill: { color: fill }, line: { color: fill, transparency: 100 },
  });
  slide.addText(text, {
    x: x + 0.07, y: y + 0.045, w: w - 0.14, h: 0.14,
    fontFace: FONT, fontSize: 7.4, color, bold: true, align: 'center', margin: 0,
  });
}

function addLabel(slide, text, x, y, w, color = C.teal) {
  slide.addText(text, {
    x, y, w, h: 0.2,
    fontFace: FONT, fontSize: 8.2, color, bold: true, margin: 0,
  });
}

function addBody(slide, text, x, y, w, h, opts = {}) {
  slide.addText(text, {
    x, y, w, h, fontFace: FONT,
    fontSize: opts.fontSize || 11,
    color: opts.color || C.ink,
    bold: opts.bold || false,
    breakLine: false,
    margin: opts.margin === undefined ? 0.04 : opts.margin,
    valign: opts.valign || 'top',
    align: opts.align || 'left',
    fit: 'shrink',
    paraSpaceAfterPt: opts.paraSpaceAfterPt || 0,
  });
}

function addBulletList(slide, bullets, x, y, w, h, opts = {}) {
  const runs = [];
  bullets.forEach((item, idx) => {
    runs.push({ text: `• ${item}${idx === bullets.length - 1 ? '' : '\n'}`, options: { breakLine: false } });
  });
  slide.addText(runs, {
    x, y, w, h, fontFace: FONT,
    fontSize: opts.fontSize || 10.5,
    color: opts.color || C.ink,
    margin: opts.margin === undefined ? 0.03 : opts.margin,
    breakLine: false,
    breakLine: false,
    valign: 'top',
    paraSpaceAfterPt: opts.paraSpaceAfterPt || 5,
    fit: 'shrink',
  });
}

function addNode(slide, label, x, y, w, h, opts = {}) {
  addCard(slide, x, y, w, h, {
    fill: opts.fill || C.white,
    line: opts.line || C.line,
  });
  if (opts.tag) addPill(slide, opts.tag, x + 0.12, y + 0.12, Math.min(w - 0.24, opts.tagW || 0.82), opts.tagColor || C.teal, opts.tagFill || C.mint);
  addBody(slide, label, x + 0.15, y + (opts.tag ? 0.48 : 0.18), w - 0.3, h - (opts.tag ? 0.58 : 0.32), {
    fontSize: opts.fontSize || 10.5,
    color: opts.color || C.navy,
    bold: opts.bold === undefined ? true : opts.bold,
    align: 'center',
    valign: 'mid',
  });
}

function addArrow(slide, x1, y1, x2, y2, color = C.muted, width = 1.35) {
  const left = Math.min(x1, x2);
  const top = Math.min(y1, y2);
  slide.addShape(pptx.ShapeType.line, {
    x: left, y: top, w: Math.abs(x2 - x1), h: Math.abs(y2 - y1),
    flipH: x2 < x1,
    flipV: y2 < y1,
    line: { color, width, endArrowType: 'triangle' },
  });
}

function addMetric(slide, x, y, number, label, color = C.teal, note = '') {
  slide.addShape(pptx.ShapeType.ellipse, {
    x, y, w: 1.36, h: 1.36,
    fill: { color: C.white }, line: { color, width: 2.1 },
  });
  addBody(slide, number, x + 0.1, y + 0.31, 1.16, 0.35, { fontSize: 19, color, bold: true, align: 'center' });
  addBody(slide, label, x + 0.11, y + 0.78, 1.14, 0.28, { fontSize: 8.3, color: C.slate, align: 'center' });
  if (note) addBody(slide, note, x - 0.28, y + 1.49, 1.92, 0.3, { fontSize: 7.2, color: C.muted, align: 'center' });
}

function addSectionTag(slide, text, x, y, w, color, fill) {
  addPill(slide, text, x, y, w, color, fill);
}

// 01 Title
{
  const s = pptx.addSlide();
  addBg(s, C.light);
  s.addShape(pptx.ShapeType.rect, { x: 0, y: 0, w: W, h: 0.16, fill: { color: C.teal }, line: { color: C.teal } });
  s.addShape(pptx.ShapeType.rect, { x: 8.65, y: 0.16, w: 4.683, h: 7.34, fill: { color: 'EEF7F6' }, line: { color: 'EEF7F6' } });
  s.addShape(pptx.ShapeType.ellipse, { x: 9.4, y: 0.72, w: 3.2, h: 3.2, line: { color: C.teal, width: 3 }, fill: { color: 'EEF7F6', transparency: 100 } });
  s.addShape(pptx.ShapeType.ellipse, { x: 10.28, y: 1.61, w: 1.43, h: 1.43, fill: { color: C.white }, line: { color: C.teal, width: 1.5 } });
  s.addShape(pptx.ShapeType.ellipse, { x: 9.45, y: 3.9, w: 0.6, h: 0.6, fill: { color: C.coral }, line: { color: C.coral } });
  s.addShape(pptx.ShapeType.ellipse, { x: 11.9, y: 4.2, w: 0.42, h: 0.42, fill: { color: C.amber }, line: { color: C.amber } });
  addArrow(s, 10.99, 3.07, 9.92, 4.0, C.teal, 1.5);
  addArrow(s, 11.48, 3.06, 12.08, 4.24, C.teal, 1.5);
  addArrow(s, 10.34, 2.5, 9.16, 2.5, C.teal, 1.5);
  s.addText('QUESTION EVOLUTION PIPELINE', { x: 0.72, y: 1.12, w: 5.7, h: 0.25, fontFace: FONT, fontSize: 10.2, color: C.teal, bold: true, charSpacing: 1.4, margin: 0 });
  s.addText('从“改写题目”到\n“发现能力边界”', { x: 0.7, y: 1.65, w: 7.4, h: 1.45, fontFace: FONT, fontSize: 31, color: C.navy, bold: true, margin: 0, breakLine: false, fit: 'shrink' });
  s.addText('Question Evolution Pipeline 项目全景分析', { x: 0.73, y: 3.35, w: 5.9, h: 0.35, fontFace: FONT, fontSize: 15, color: C.slate, margin: 0 });
  s.addShape(pptx.ShapeType.line, { x: 0.73, y: 4.12, w: 1.25, h: 0, line: { color: C.teal, width: 3 } });
  s.addText('目标：以真实评分反馈驱动题目进化，构造更能区分模型能力的训练与评测数据。', { x: 0.73, y: 4.37, w: 6.85, h: 0.55, fontFace: FONT, fontSize: 12.2, color: C.ink, margin: 0, fit: 'shrink' });
  addPill(s, '架构 / 机制 / 效率 / 风险', 0.73, 5.35, 2.25, C.navy, 'EAF3FA');
  s.addText('2026.07  ·  基于当前代码与测试快照', { x: 0.73, y: 6.78, w: 3.4, h: 0.18, fontFace: FONT, fontSize: 7.6, color: C.muted, margin: 0 });
}

// 02 Positioning
{
  const s = pptx.addSlide(); addBg(s); addHeader(s, '01 · POSITIONING', '项目定位：一套“实验驱动”的能力边界发现系统', '不是把题目机械写长，而是通过真实评测不断逼近弱模型的推理边界。');
  addCard(s, 0.72, 1.95, 3.74, 3.62, { fill: 'FBFCFE', line: C.line });
  addSectionTag(s, '传统改写', 0.98, 2.22, 0.92, C.slate, 'EDF2F7');
  addBody(s, '问题常被理解为：\n“如何把原题变得更复杂？”', 0.98, 2.73, 2.98, 0.62, { fontSize: 14.2, color: C.navy, bold: true });
  addBulletList(s, ['容易堆叠条件与术语', '可能只增加阅读负担', '无法证明真实难度提高'], 0.98, 3.68, 2.95, 1.1, { fontSize: 10.3, color: C.slate });
  addArrow(s, 4.66, 3.72, 5.6, 3.72, C.teal, 2.25);
  s.addShape(pptx.ShapeType.ellipse, { x: 5.15, y: 3.22, w: 1.0, h: 1.0, fill: { color: C.mint }, line: { color: C.teal, width: 1.2 } });
  addBody(s, '转向', 5.31, 3.54, 0.68, 0.18, { fontSize: 10, color: C.teal, bold: true, align: 'center' });
  addCard(s, 6.07, 1.95, 6.5, 3.62, { fill: 'F5FBFA', line: 'B8E2DB' });
  addSectionTag(s, 'Question Evolution', 6.36, 2.22, 1.53, C.teal, C.mint);
  addBody(s, '核心问题：\n“哪种改写能让候选模型暴露出可解释的能力缺口？”', 6.36, 2.73, 5.62, 0.62, { fontSize: 14.2, color: C.navy, bold: true });
  const facts = [
    ['真实性', '新题仍然可答、事实不失真'],
    ['区分度', '候选模型得分下降或打破满分'],
    ['可解释', '错误方向命中预期能力轴'],
  ];
  facts.forEach((item, i) => {
    const yy = 3.67 + i * 0.55;
    s.addShape(pptx.ShapeType.ellipse, { x: 6.38, y: yy + 0.03, w: 0.18, h: 0.18, fill: { color: [C.teal, C.blue, C.coral][i] }, line: { color: [C.teal, C.blue, C.coral][i] } });
    addBody(s, item[0], 6.72, yy, 0.7, 0.22, { fontSize: 10.2, color: C.navy, bold: true });
    addBody(s, item[1], 7.52, yy, 3.9, 0.25, { fontSize: 10.2, color: C.slate });
  });
  addPill(s, '自动评分 = 边界候选证据；人工复核 = 最终确认', 3.6, 6.03, 6.2, C.navy, 'EAF3FA');
  addFooter(s, 2);
}

// 03 Architecture
{
  const s = pptx.addSlide(); addBg(s); addHeader(s, '02 · ARCHITECTURE', '总体架构：一个闭环，两种搜索模式', '统一的数据与评测契约；根据实验目标选择链式进化、横向分支或纵向算子叠加。');
  const xs = [0.65, 2.25, 3.85, 5.45, 7.05, 8.65, 10.25];
  const nodes = [
    ['已准入\n种子题', '输入'], ['Round 0\n稳定评分', '基线'], ['画像与\n进化分流', '诊断'], ['混合\n算子路由', '决策'], ['候选生成\n与校验', '生成'], ['完整\n重评测', '评测'], ['效果反馈\n与 Memory', '学习'],
  ];
  nodes.forEach((n, i) => {
    addNode(s, n[0], xs[i], 2.28, 1.28, 1.06, { tag: n[1], tagW: 0.65, fontSize: 10.2, fill: i === 6 ? 'F5FBFA' : C.white, line: i === 6 ? 'B8E2DB' : C.line });
    if (i < nodes.length - 1) addArrow(s, xs[i] + 1.3, 2.81, xs[i + 1] - 0.07, 2.81, C.muted, 1.1);
  });
  addArrow(s, 11.53, 3.43, 3.88, 4.47, C.teal, 1.3);
  addCard(s, 0.75, 4.35, 3.76, 1.57, { fill: 'FBFCFE' });
  addPill(s, '链式核心模式', 1.0, 4.62, 1.22, C.blue, C.sky);
  addBody(s, '单轮局部多候选 → 选 1 条主链 →\n效果分析与状态更新 → 下一轮', 1.0, 5.08, 3.1, 0.48, { fontSize: 10.3, color: C.ink });
  addCard(s, 4.76, 4.35, 3.76, 1.57, { fill: 'F5FBFA', line: 'B8E2DB' });
  addPill(s, '横向分支搜索', 5.02, 4.62, 1.27, C.teal, C.mint);
  addBody(s, '同一父题的多个路由算子形成分支；\n每个分支均完成独立评测闭环。', 5.02, 5.08, 3.15, 0.48, { fontSize: 10.3, color: C.ink });
  addCard(s, 8.77, 4.35, 3.76, 1.57, { fill: 'FFFAF0', line: 'F4D796' });
  addPill(s, '纵向算子叠加', 9.03, 4.62, 1.36, 'A66B16', C.yellow);
  addBody(s, '仅对已降分 frontier 重新画像、路由；\n默认最多两次连续算子。', 9.03, 5.08, 3.16, 0.48, { fontSize: 10.3, color: C.ink });
  addPill(s, '当前 run_loop 默认：multi_operator_branch · step · branch window = 1', 2.93, 6.18, 7.4, C.navy, 'EAF3FA');
  addFooter(s, 3);
}

// 04 Closed loop
{
  const s = pptx.addSlide(); addBg(s); addHeader(s, '03 · CLOSED LOOP', '核心闭环：每一次“变难”都必须被重新验证', '题目变化后，旧答案、旧 Rubric 与旧分数均不再直接可信。');
  const steps = [
    ['1', '发现', '稳定高分\n或可探测边界'],
    ['2', '诊断', '定位浅层线索\n与能力缺口'],
    ['3', '改写', '算子生成\n局部多候选'],
    ['4', '重建', '新参考答案\n+ 新 Rubric'],
    ['5', '压测', '多 Trial\n重新评分'],
    ['6', '学习', '效果判断\n+ Memory'],
  ];
  steps.forEach((st, i) => {
    const x = 0.64 + i * 2.08;
    s.addShape(pptx.ShapeType.ellipse, { x, y: 2.09, w: 0.52, h: 0.52, fill: { color: i === 5 ? C.teal : C.sky }, line: { color: i === 5 ? C.teal : C.blue, width: 1 } });
    addBody(s, st[0], x, 2.23, 0.52, 0.15, { fontSize: 9.5, color: i === 5 ? C.white : C.blue, bold: true, align: 'center' });
    addBody(s, st[1], x - 0.03, 2.78, 0.62, 0.2, { fontSize: 10.2, color: C.navy, bold: true, align: 'center' });
    addBody(s, st[2], x - 0.46, 3.13, 1.45, 0.55, { fontSize: 9.7, color: C.slate, align: 'center' });
    if (i < steps.length - 1) addArrow(s, x + 0.63, 2.35, x + 1.69, 2.35, C.muted, 1.25);
  });
  s.addShape(pptx.ShapeType.line, { x: 1.28, y: 4.88, w: 10.06, h: 0, line: { color: C.teal, width: 1.7, dash: 'dash' } });
  addArrow(s, 1.43, 4.95, 1.1, 4.2, C.teal, 1.25);
  addCard(s, 2.12, 5.42, 8.98, 0.72, { fill: 'F5FBFA', line: 'B8E2DB' });
  addBody(s, '关键不变量：候选是否“有效”，优先由后验真实评分决定；前置 Validator 只排除致命风险与提供分流信号。', 2.38, 5.66, 8.44, 0.2, { fontSize: 10.6, color: C.navy, bold: true, align: 'center' });
  addFooter(s, 4);
}

// 05 Round 0
{
  const s = pptx.addSlide(); addBg(s); addHeader(s, '04 · ADMISSION', 'Round 0：先判断“高分是否稳定”，再投入进化成本', '通过多次独立回答与重复评分，避免偶然高分、偶然低分和不稳定样本误导后续策略。');
  addCard(s, 0.72, 2.0, 4.16, 3.78, { fill: 'FBFCFE' });
  addPill(s, '默认评测矩阵', 1.02, 2.29, 1.08, C.blue, C.sky);
  addMetric(s, 1.08, 2.93, '3×2', 'Qwen 回答 / 自评', C.blue, '6 次 Qwen 相关调用');
  addMetric(s, 2.78, 2.93, '3×2', 'GPT 回答 / 自评', C.teal, '6 次 GPT 相关调用');
  addBody(s, 'Round 0 每题默认：6 次回答 + 12 次评分 = 18 次模型调用', 1.02, 4.78, 3.48, 0.25, { fontSize: 10.1, color: C.navy, bold: true, align: 'center' });
  addCard(s, 5.18, 2.0, 3.0, 3.78, { fill: 'F5FBFA', line: 'B8E2DB' });
  addPill(s, '稳定性统计', 5.46, 2.29, 0.98, C.teal, C.mint);
  addBulletList(s, ['均值 / 中位数 / P75', '标准差、极差、满分次数', 'Rubric 条目稳定性', '推荐候选预算'], 5.46, 2.85, 2.2, 1.76, { fontSize: 10.4 });
  addCard(s, 8.48, 2.0, 4.11, 3.78, { fill: 'FFFCF5', line: 'F4D796' });
  addPill(s, '准入状态', 8.78, 2.29, 0.83, 'A66B16', C.yellow);
  const statuses = [
    ['stable_high', '稳定高分，优先进化', C.teal],
    ['unstable_high', '不稳但有强高分信号', C.blue],
    ['borderline_probe', '边界附近，低预算探测', C.amber],
    ['stable_low / uncertain_low', '停止或人工复核', C.coral],
  ];
  statuses.forEach((it, i) => {
    const yy = 2.88 + i * 0.54;
    s.addShape(pptx.ShapeType.ellipse, { x: 8.8, y: yy + 0.05, w: 0.16, h: 0.16, fill: { color: it[2] }, line: { color: it[2] } });
    addBody(s, it[0], 9.08, yy, 1.2, 0.2, { fontSize: 9.5, color: C.navy, bold: true });
    addBody(s, it[1], 10.32, yy, 1.83, 0.2, { fontSize: 9.5, color: C.slate });
  });
  addFooter(s, 5);
}

// 06 Profile and actions
{
  const s = pptx.addSlide(); addBg(s); addHeader(s, '05 · DIAGNOSIS', '画像与分流：不是“高分就改”，而是选择最有信息价值的样本', '系统综合当前得分、稳定性、候选回答、过分诊断和跨轮状态，决定下一步动作。');
  addCard(s, 0.7, 1.98, 3.22, 4.15, { fill: 'FBFCFE' });
  addPill(s, '样本画像输入', 0.98, 2.26, 1.03, C.blue, C.sky);
  addBulletList(s, ['原题与参考答案', '候选模型回答', '逐条 Rubric 得分', 'Round 0 稳定性信息', '上一轮状态与失败经验'], 0.98, 2.83, 2.35, 2.2, { fontSize: 10.6 });
  addArrow(s, 4.12, 4.0, 5.0, 4.0, C.teal, 1.7);
  addCard(s, 5.15, 1.98, 2.56, 4.15, { fill: 'F5FBFA', line: 'B8E2DB' });
  addPill(s, '诊断输出', 5.44, 2.26, 0.87, C.teal, C.mint);
  addBody(s, 'sample_profile', 5.45, 2.92, 1.85, 0.23, { fontSize: 10.4, color: C.navy, bold: true, align: 'center' });
  addBody(s, 'overscore_diagnosis', 5.34, 3.43, 2.05, 0.23, { fontSize: 10.4, color: C.navy, bold: true, align: 'center' });
  addBody(s, '定位：模型为什么答对？\n下一步应压测什么？', 5.35, 4.2, 2.05, 0.52, { fontSize: 9.8, color: C.slate, align: 'center' });
  addArrow(s, 7.91, 4.0, 8.69, 4.0, C.teal, 1.7);
  const actionRows = [
    ['高分进化', 'evolve_high_score_overscore', C.teal, '明显虚高'],
    ['低分重构', 'reconstruct_low_score_boundary', C.blue, '有边界信号'],
    ['中分探测', 'probe_middle_score_boundary', C.amber, '有限探索'],
    ['原题透传', 'pass_through_or_scoring_noise', C.slate, '无价值 / 噪声'],
    ['停止进化', 'stop_evolution', C.coral, '终止状态'],
  ];
  actionRows.forEach((r, i) => {
    const yy = 2.08 + i * 0.72;
    addCard(s, 8.82, yy, 3.72, 0.55, { fill: i === 0 ? 'F5FBFA' : C.white, line: i === 0 ? 'B8E2DB' : C.line });
    s.addShape(pptx.ShapeType.rect, { x: 8.83, y: yy, w: 0.08, h: 0.55, fill: { color: r[2] }, line: { color: r[2] } });
    addBody(s, r[0], 9.1, yy + 0.13, 0.86, 0.2, { fontSize: 9.5, color: C.navy, bold: true });
    addBody(s, r[3], 10.04, yy + 0.13, 0.72, 0.2, { fontSize: 8.8, color: C.slate });
    addBody(s, r[1], 10.77, yy + 0.14, 1.55, 0.17, { fontSize: 7.0, color: C.muted, align: 'right' });
  });
  addFooter(s, 6);
}

// 07 operators
{
  const s = pptx.addSlide(); addBg(s); addHeader(s, '06 · OPERATORS', '算子体系：把“如何变难”拆成可控的能力轴', '当前 O10–O33 共 24 个算子；内部控制推理结构，但避免把答题脚手架直接写进题面。');
  const ops = [
    ['证据与闭合', '最小充分证据、缺失链路、观察可靠性、信息增量', 'O10 / O13 / O14 / O23 / O28 / O31', C.teal, C.mint],
    ['时空与实体', '时间窗、路径、对象来源、角色绑定、跨模态一致性', 'O11 / O19–O22 / O29 / O32 / O33', C.blue, C.sky],
    ['阈值与校准', '反事实、量化传播、事实与行动、跨层结论边界', 'O12 / O15 / O17 / O25–O27', C.amber, C.yellow],
    ['竞争解释', '近似替代、残差比较、基线范围、主动区分观察', 'O16 / O18 / O24 / O30', C.coral, C.redBg],
  ];
  ops.forEach((op, i) => {
    const x = 0.72 + (i % 2) * 6.05;
    const y = 2.02 + Math.floor(i / 2) * 1.9;
    addCard(s, x, y, 5.55, 1.46, { fill: op[4], line: op[3] });
    s.addShape(pptx.ShapeType.ellipse, { x: x + 0.26, y: y + 0.31, w: 0.52, h: 0.52, fill: { color: op[3] }, line: { color: op[3] } });
    addBody(s, String(i + 1), x + 0.26, y + 0.46, 0.52, 0.12, { fontSize: 8, color: C.white, bold: true, align: 'center' });
    addBody(s, op[0], x + 1.02, y + 0.24, 2.0, 0.26, { fontSize: 13.2, color: C.navy, bold: true });
    addBody(s, op[1], x + 1.02, y + 0.65, 3.92, 0.34, { fontSize: 9.5, color: C.ink });
    addBody(s, op[2], x + 1.02, y + 1.12, 3.98, 0.16, { fontSize: 7.4, color: C.slate });
  });
  addCard(s, 0.72, 5.98, 11.56, 0.58, { fill: 'FBFCFE' });
  addBody(s, '题面约束：不显式要求 A/B 二选一、层级排序、双门槛或动作层标签化作答；保留真实的证据关系、结论承接与事实绑定难度。', 0.98, 6.18, 11.05, 0.18, { fontSize: 9.7, color: C.navy, bold: true, align: 'center' });
  addFooter(s, 7);
}

// 08 hybrid router
{
  const s = pptx.addSlide(); addBg(s); addHeader(s, '07 · ROUTING', '混合路由：规则负责边界，LLM 负责语义匹配，Memory 提供经验', 'Router 输出经 Schema 校验，并冻结为当前父题可调度的候选算子列表。');
  const sources = [
    ['规则约束', '算子启用状态\navoid / 终态 / 重复限制', C.blue, C.sky],
    ['LLM Router', '当前题面 + 画像 + 证据片段\n输出候选算子及理由', C.teal, C.mint],
    ['Memory Bank', '历史成功 / 失败形态\n同类样本匹配与缓存', C.amber, C.yellow],
  ];
  sources.forEach((src, i) => {
    const x = 0.78 + i * 3.05;
    addCard(s, x, 2.12, 2.55, 1.58, { fill: src[3], line: src[2] });
    addBody(s, src[0], x + 0.18, 2.4, 2.18, 0.25, { fontSize: 12, color: C.navy, bold: true, align: 'center' });
    addBody(s, src[1], x + 0.22, 2.91, 2.1, 0.44, { fontSize: 9.4, color: C.ink, align: 'center' });
    addArrow(s, x + 1.27, 3.78, 6.65, 4.34, src[2], 1.2);
  });
  addCard(s, 9.96, 2.12, 2.56, 1.58, { fill: 'FBFCFE', line: C.navy });
  addBody(s, '严格契约', 10.2, 2.4, 2.08, 0.25, { fontSize: 12, color: C.navy, bold: true, align: 'center' });
  addBody(s, '证据片段必须来自输入；\n不合法候选单独审计。', 10.2, 2.91, 2.08, 0.43, { fontSize: 9.4, color: C.ink, align: 'center' });
  addArrow(s, 11.24, 3.78, 6.65, 4.34, C.navy, 1.2);
  s.addShape(pptx.ShapeType.ellipse, { x: 5.48, y: 4.06, w: 2.32, h: 1.02, fill: { color: C.white }, line: { color: C.teal, width: 1.6 } });
  addBody(s, 'operator_route', 5.68, 4.37, 1.92, 0.22, { fontSize: 13, color: C.teal, bold: true, align: 'center' });
  addBody(s, 'primary · backup · avoid\n冻结 selected_operator_ids', 4.45, 5.35, 4.32, 0.5, { fontSize: 10.2, color: C.slate, align: 'center' });
  addPill(s, '避免“注册表里的所有算子”被自动加入搜索计划', 3.65, 6.14, 5.8, C.navy, 'EAF3FA');
  addFooter(s, 8);
}

// 09 Candidate validation
{
  const s = pptx.addSlide(); addBg(s); addHeader(s, '08 · SELECTION', '候选选择：用“分流”保留探索，而不是把所有不确定项一刀切', '每个算子可生成候选；复杂度、事实和收益校验后，再按主链与探索预算选择。');
  const lanes = [
    ['Hard reject', '不可答 / 事实错误 / 严重模板化', C.coral, C.redBg, '停止，不进入评分'],
    ['Main chain', 'clear_gain / probable_gain 等明确收益', C.teal, C.mint, '进入完整主链评分'],
    ['Exploration', 'weak_gain / needs_manual_review 等不确定收益', C.amber, C.yellow, '预算内进入真实评分'],
    ['Pass through', '无探索价值或原始样本', C.slate, 'EDF2F7', '保留原题及已有结果'],
  ];
  lanes.forEach((ln, i) => {
    const y = 1.98 + i * 1.05;
    addCard(s, 0.72, y, 11.85, 0.75, { fill: ln[3], line: ln[2] });
    s.addShape(pptx.ShapeType.rect, { x: 0.73, y, w: 0.13, h: 0.75, fill: { color: ln[2] }, line: { color: ln[2] } });
    addBody(s, ln[0], 1.1, y + 0.22, 1.5, 0.2, { fontSize: 11.4, color: C.navy, bold: true });
    addBody(s, ln[1], 3.05, y + 0.22, 4.0, 0.2, { fontSize: 9.8, color: C.ink });
    addArrow(s, 7.48, y + 0.38, 8.27, y + 0.38, ln[2], 1.1);
    addBody(s, ln[4], 8.55, y + 0.22, 3.25, 0.2, { fontSize: 9.8, color: C.navy, bold: true });
  });
  addCard(s, 1.87, 6.26, 9.58, 0.46, { fill: 'FBFCFE' });
  addBody(s, '控制规则：每个 candidate group 最多选 1 个 exploration candidate；每轮 exploration 数量受总预算限制。', 2.12, 6.39, 9.08, 0.16, { fontSize: 9.2, color: C.slate, align: 'center' });
  addFooter(s, 9);
}

// 10 rebuild and evaluation
{
  const s = pptx.addSlide(); addBg(s); addHeader(s, '09 · EVALUATION', '评测闭环：题目变了，参考答案、Rubric 与评分 Prompt 必须全部重建', '旧评分材料仅作为 stale 历史保留；新题必须建立独立、可追溯的评测上下文。');
  addCard(s, 0.72, 2.0, 3.25, 3.8, { fill: 'FBFCFE' });
  addPill(s, '题目进化后', 1.0, 2.27, 0.95, C.coral, C.redBg);
  addBody(s, '旧材料失效', 1.0, 2.88, 2.55, 0.28, { fontSize: 16, color: C.navy, bold: true, align: 'center' });
  addBulletList(s, ['旧 reference', '旧 rubric', '旧 score_prompt', '旧 scoring_result'], 1.14, 3.55, 1.75, 1.4, { fontSize: 10.5, color: C.slate });
  addArrow(s, 4.15, 3.9, 5.13, 3.9, C.teal, 1.8);
  const rebuild = [
    ['1', '生成新参考答案', C.teal, C.mint],
    ['2', '生成新 Rubric', C.blue, C.sky],
    ['3', '生成新评分 Prompt', C.amber, C.yellow],
  ];
  rebuild.forEach((r, i) => {
    const y = 2.01 + i * 1.15;
    addCard(s, 5.35, y, 3.15, 0.81, { fill: r[3], line: r[2] });
    s.addShape(pptx.ShapeType.ellipse, { x: 5.62, y: y + 0.19, w: 0.4, h: 0.4, fill: { color: r[2] }, line: { color: r[2] } });
    addBody(s, r[0], 5.62, y + 0.32, 0.4, 0.1, { fontSize: 7.4, color: C.white, bold: true, align: 'center' });
    addBody(s, r[1], 6.28, y + 0.27, 1.78, 0.2, { fontSize: 11, color: C.navy, bold: true });
  });
  addArrow(s, 8.72, 3.9, 9.6, 3.9, C.teal, 1.8);
  addCard(s, 9.82, 2.0, 2.76, 3.8, { fill: 'F5FBFA', line: 'B8E2DB' });
  addPill(s, '重新评分', 10.1, 2.27, 0.83, C.teal, C.mint);
  addBody(s, '新题\n+ 新答案\n+ 新标准', 10.2, 3.05, 1.95, 0.75, { fontSize: 17, color: C.navy, bold: true, align: 'center' });
  addBody(s, '避免“题目已变，但仍用旧标准评分”的伪实验。', 10.15, 4.55, 2.03, 0.45, { fontSize: 9.7, color: C.slate, align: 'center' });
  addFooter(s, 10);
}

// 11 Scoring protocol
{
  const s = pptx.addSlide(); addBg(s); addHeader(s, '10 · SCORING', '多 Trial、双 Judge：在线决策与实验观察严格分离', '在线 score_rate 只使用 Qwen 回答 + Qwen Judge；GPT 结果保留为实验性对照。');
  const cols = [
    ['回答轨道', 'Qwen：3 个独立回答\nGPT：3 个独立回答', C.blue, C.sky],
    ['评分轨道', 'Qwen 自评：每份 2 次\nGPT 自评：每份 2 次\nGPT 复评 Qwen：每份 2 次', C.teal, C.mint],
    ['聚合与决策', 'Qwen 自评全量聚合\n→ 顶层 score_rate\nGPT 汇总仅供对照', C.amber, C.yellow],
  ];
  cols.forEach((c, i) => {
    const x = 0.73 + i * 4.13;
    addCard(s, x, 2.02, 3.62, 2.6, { fill: c[3], line: c[2] });
    addPill(s, c[0], x + 0.27, 2.31, 0.95, c[2], c[3]);
    addBody(s, c[1], x + 0.34, 2.96, 2.95, 0.92, { fontSize: 11.3, color: C.navy, bold: true, align: 'center' });
    if (i < 2) addArrow(s, x + 3.66, 3.33, x + 4.04, 3.33, C.muted, 1.2);
  });
  addCard(s, 0.73, 5.02, 11.9, 0.92, { fill: 'FBFCFE' });
  addBody(s, '调用规模', 1.1, 5.27, 1.02, 0.2, { fontSize: 11.5, color: C.navy, bold: true });
  addBody(s, 'Round 0：18 次 / 题', 2.42, 5.27, 2.0, 0.2, { fontSize: 11.5, color: C.blue, bold: true });
  addBody(s, '进化后完整分支：24 次 / 分支', 5.04, 5.27, 3.12, 0.2, { fontSize: 11.5, color: C.teal, bold: true });
  addBody(s, '结论：远程模型调用是主要成本，本地 JSONL 和校验通常不是首要瓶颈。', 8.25, 5.27, 3.8, 0.2, { fontSize: 9.2, color: C.slate, align: 'right' });
  addPill(s, 'GPT 不会覆盖、平均或修改在线 score_rate', 4.05, 6.25, 5.25, C.navy, 'EAF3FA');
  addFooter(s, 11);
}

// 12 Feedback
{
  const s = pptx.addSlide(); addBg(s); addHeader(s, '11 · FEEDBACK', '效果判定与反馈：得分上升是负收益，必须回滚而非“成功终止”', '系统同时看前后得分、校验通过情况、题型重复风险和错误方向是否命中预期 focus。');
  addCard(s, 0.75, 2.05, 3.33, 3.86, { fill: 'F5FBFA', line: 'B8E2DB' });
  addPill(s, '有效边界候选', 1.02, 2.34, 1.1, C.teal, C.mint);
  addBulletList(s, ['题目确已进化', '复杂度 / 可答性通过', '非重复题型', '得分下降或打破满分', '错误方向命中 expected focus'], 1.0, 2.93, 2.42, 2.02, { fontSize: 10.2 });
  addBody(s, '输出：effective_boundary_probe\n（仍需人工复核）', 1.0, 5.24, 2.55, 0.36, { fontSize: 9.2, color: C.teal, bold: true, align: 'center' });
  addCard(s, 4.98, 2.05, 3.33, 3.86, { fill: 'FFF9E9', line: 'F4D796' });
  addPill(s, '不确定结果', 5.25, 2.34, 0.94, 'A66B16', C.yellow);
  addBulletList(s, ['得分下降幅度小', 'focus 不充分或不匹配', '评分变化难以解释', '进入 needs_manual_review'], 5.23, 2.95, 2.36, 1.55, { fontSize: 10.2 });
  addBody(s, '原则：不把不确定候选误判为强成功经验。', 5.23, 5.14, 2.4, 0.32, { fontSize: 9.2, color: 'A66B16', bold: true, align: 'center' });
  addCard(s, 9.22, 2.05, 3.33, 3.86, { fill: C.redBg, line: 'F1B2AA' });
  addPill(s, 'score_increased', 9.49, 2.34, 1.02, C.coral, C.redBg);
  addBody(s, '进化后得分升高\n= 当前题目更容易', 9.52, 3.0, 2.73, 0.6, { fontSize: 15, color: C.navy, bold: true, align: 'center' });
  addArrow(s, 10.88, 3.85, 10.88, 4.4, C.coral, 1.5);
  addBody(s, '恢复直接父题\n写入 failure memory\n下一轮换算子', 9.52, 4.58, 2.72, 0.65, { fontSize: 10.2, color: C.coral, bold: true, align: 'center' });
  addFooter(s, 12);
}

// 13 Horizontal search
{
  const s = pptx.addSlide(); addBg(s); addHeader(s, '12 · HORIZONTAL SEARCH', '横向多算子分支搜索：同一父题，并行探索不同能力轴', '分支不来自全量注册表，而只来自 Router 冻结后的 selected_operator_ids。');
  addNode(s, '父题\n已评分', 0.72, 3.04, 1.25, 0.98, { tag: 'root', tagW: 0.55, fill: 'FBFCFE' });
  addArrow(s, 2.05, 3.53, 3.03, 2.28, C.muted, 1.25);
  addArrow(s, 2.05, 3.53, 3.03, 3.53, C.muted, 1.25);
  addArrow(s, 2.05, 3.53, 3.03, 4.78, C.muted, 1.25);
  const br = [
    ['O10', '完整闭环\n→ 降分？', 2.0, C.teal, C.mint],
    ['O15', '完整闭环\n→ 降分？', 3.25, C.blue, C.sky],
    ['O24', '完整闭环\n→ 降分？', 4.5, C.amber, C.yellow],
  ];
  br.forEach((b, i) => {
    addNode(s, b[1], 3.32, b[2] - 0.45, 1.54, 0.9, { tag: b[0], tagW: 0.54, tagColor: b[3], tagFill: b[4], fontSize: 9.4, fill: C.white });
    addArrow(s, 4.98, b[2], 6.03, b[2], b[3], 1.25);
    addCard(s, 6.2, b[2] - 0.35, 2.08, 0.7, { fill: b[4], line: b[3] });
    addBody(s, i === 1 ? '形成边界候选\n或普通终态' : '状态归并\n更新分支结果', 6.42, b[2] - 0.17, 1.64, 0.32, { fontSize: 8.7, color: C.navy, bold: true, align: 'center' });
  });
  addCard(s, 8.9, 2.2, 3.56, 2.83, { fill: 'FBFCFE' });
  addPill(s, '动态窗口与幂等', 9.18, 2.49, 1.12, C.navy, 'EAF3FA');
  addBulletList(s, ['稳定 branch_id = parent + operator', '窗口受剩余边界名额限制', '已完成分支不重复调度', 'Qwen 决策完成即可更新状态'], 9.15, 3.04, 2.65, 1.28, { fontSize: 9.6 });
  addPill(s, 'step：逐阶段编排  ·  stream：有界队列、逐分支补位', 2.72, 6.0, 7.78, C.teal, C.mint);
  addFooter(s, 13);
}

// 14 vertical search
{
  const s = pptx.addSlide(); addBg(s); addHeader(s, '13 · VERTICAL SEARCH', '纵向算子叠加：只有已降分的节点，才有资格进入下一层探索', '目标是发现单一算子难以形成的复合能力边界，而不是无限树搜索。');
  addNode(s, 'root\n基线题', 0.78, 3.03, 1.42, 0.93, { tag: 'depth 1', tagW: 0.7, fill: 'FBFCFE' });
  addArrow(s, 2.32, 3.49, 3.25, 2.47, C.muted, 1.3);
  addArrow(s, 2.32, 3.49, 3.25, 4.5, C.muted, 1.3);
  addNode(s, 'O10\n降分', 3.5, 2.02, 1.52, 0.91, { tag: 'depth 2', tagW: 0.7, tagColor: C.teal, tagFill: C.mint, fill: 'F5FBFA', line: 'B8E2DB' });
  addNode(s, 'O24\n不降分', 3.5, 4.05, 1.52, 0.91, { tag: 'depth 2', tagW: 0.7, tagColor: C.slate, tagFill: 'EDF2F7', fill: C.white });
  addArrow(s, 5.13, 2.47, 6.07, 2.47, C.teal, 1.4);
  addCard(s, 6.32, 1.78, 2.02, 1.37, { fill: C.mint, line: C.teal });
  addBody(s, 'frontier\n重新画像 + 重新路由', 6.57, 2.16, 1.53, 0.48, { fontSize: 10.2, color: C.navy, bold: true, align: 'center' });
  addArrow(s, 8.45, 2.47, 9.35, 1.78, C.teal, 1.25);
  addArrow(s, 8.45, 2.47, 9.35, 3.18, C.teal, 1.25);
  addNode(s, 'O10 → O15\n继续降分', 9.55, 1.3, 2.1, 0.94, { tag: 'depth 3', tagW: 0.7, tagColor: C.amber, tagFill: C.yellow, fill: 'FFFCF5', line: C.amber, fontSize: 9.3 });
  addNode(s, 'O10 → O30\n不降分', 9.55, 2.74, 2.1, 0.94, { tag: 'depth 3', tagW: 0.7, tagColor: C.slate, tagFill: 'EDF2F7', fontSize: 9.3 });
  addCard(s, 0.85, 5.56, 11.55, 0.66, { fill: 'FBFCFE' });
  addBody(s, '默认约束：最大深度 3；只比较直接父子分数；路径内不重复算子；单算子、叠加算子及总边界均设独立配额。', 1.15, 5.79, 10.95, 0.18, { fontSize: 9.8, color: C.navy, bold: true, align: 'center' });
  addFooter(s, 14);
}

// 15 Efficiency
{
  const s = pptx.addSlide(); addBg(s); addHeader(s, '14 · EFFICIENCY', '效率优化：优化等待、重复与恢复，不牺牲实验协议', '系统的主要瓶颈是远程模型服务；优化重点是减少无价值调用与服务端排队。');
  const eff = [
    ['少做无价值调用', '稳定性准入、候选预算、探索预算、透传复用', C.teal],
    ['分离两种并发', '样本 worker ≠ 实际在途请求；Qwen/GPT 各自公平请求池', C.blue],
    ['避免重复工作', 'Router Cache、Memory 倒排索引、本地校验规则版本复用', C.amber],
    ['快速恢复运行', 'partial + checkpoint + manifest + 原子发布', C.coral],
    ['提升搜索吞吐', '动态分支窗口、stream 管线、GPT 实验评分解耦', C.teal],
    ['控制产物规模', 'trace sidecar、compact 分支产物、轻量搜索状态', C.blue],
  ];
  eff.forEach((e, i) => {
    const x = 0.74 + (i % 2) * 6.15;
    const y = 1.96 + Math.floor(i / 2) * 1.38;
    addCard(s, x, y, 5.63, 1.06, { fill: i % 2 === 0 ? 'FBFCFE' : C.white, line: C.line });
    s.addShape(pptx.ShapeType.rect, { x, y, w: 0.12, h: 1.06, fill: { color: e[2] }, line: { color: e[2] } });
    addBody(s, e[0], x + 0.4, y + 0.24, 1.65, 0.22, { fontSize: 11.3, color: C.navy, bold: true });
    addBody(s, e[1], x + 2.1, y + 0.23, 3.1, 0.38, { fontSize: 9.2, color: C.slate });
  });
  addCard(s, 1.52, 6.17, 10.35, 0.48, { fill: 'F5FBFA', line: 'B8E2DB' });
  addBody(s, '性能事件会记录解析、排队、远程等待、重试、写入、队列深度与 RSS 峰值，使瓶颈可被量化定位。', 1.79, 6.31, 9.8, 0.16, { fontSize: 9.3, color: C.navy, align: 'center' });
  addFooter(s, 15);
}

// 16 Reliability
{
  const s = pptx.addSlide(); addBg(s); addHeader(s, '15 · RELIABILITY', '工程可靠性：可恢复、可审计、可复现', '阶段间采用 JSONL 契约，正式产物只有在完整校验后才对下游可见。');
  const artifact = [
    ['输入', 'input.jsonl', C.slate], ['运行中', '.partial', C.amber], ['恢复点', '.checkpoint.jsonl', C.blue], ['正式发布', 'output.jsonl', C.teal], ['审计', '.manifest.json\ntrace sidecar', C.coral],
  ];
  artifact.forEach((a, i) => {
    const x = 0.68 + i * 2.48;
    addNode(s, a[1], x, 2.4, 1.78, 0.88, { tag: a[0], tagW: 0.7, tagColor: a[2], tagFill: a[2] === C.teal ? C.mint : a[2] === C.blue ? C.sky : a[2] === C.amber ? C.yellow : a[2] === C.coral ? C.redBg : 'EDF2F7', fontSize: 9.0, fill: C.white });
    if (i < artifact.length - 1) addArrow(s, x + 1.84, 2.84, x + 2.36, 2.84, C.muted, 1.2);
  });
  const features = [
    ['Fail-fast', '任一 API 阶段失败写入 *.failed，并以非零状态退出；禁止不完整正式输出进入下游。'],
    ['Trace 分层', '原始模型响应存入压缩 sidecar，主业务 JSONL 只保留 trace ID，降低复制与存储成本。'],
    ['Memory 幂等', 'operator / failure / invalid 三类 Memory 通过稳定键写入，断点恢复不重复追加。'],
  ];
  features.forEach((f, i) => {
    const y = 4.05 + i * 0.61;
    s.addShape(pptx.ShapeType.ellipse, { x: 1.04, y: y + 0.07, w: 0.18, h: 0.18, fill: { color: [C.teal, C.blue, C.coral][i] }, line: { color: [C.teal, C.blue, C.coral][i] } });
    addBody(s, f[0], 1.48, y, 1.1, 0.22, { fontSize: 10.2, color: C.navy, bold: true });
    addBody(s, f[1], 2.8, y, 8.95, 0.28, { fontSize: 9.4, color: C.slate });
  });
  addFooter(s, 16);
}

// 17 Risks & roadmap
{
  const s = pptx.addSlide(); addBg(s); addHeader(s, '16 · NEXT', '关键风险与后续演进：从“可运行”走向“可规模化验证”', '当前系统已具备完整闭环与受控搜索能力，下一步重点是评测可信度、成本控制与实验治理。');
  const risks = [
    ['在线 Judge 偏差', 'Qwen 同时承担回答与在线评分；GPT 目前仅为实验对照。', '建议：盲审、一致性指标、人工抽检'],
    ['降分不必然等于变难', '仍可能来自歧义、评分错配或随机波动。', '建议：强化 focus 对齐与人工审核闭环'],
    ['模型调用成本高', '单条完整进化分支仅评分就至少 24 次调用。', '建议：预算、缓存、性能事件、分批灰度'],
    ['Memory 路由偏置', '历史经验会加速收敛，也可能固化早期偏好。', '建议：按算子 / 场景监控收益与覆盖'],
  ];
  risks.forEach((r, i) => {
    const y = 1.94 + i * 1.0;
    addCard(s, 0.73, y, 11.95, 0.74, { fill: i % 2 === 0 ? 'FBFCFE' : C.white });
    addBody(s, r[0], 1.02, y + 0.23, 1.72, 0.18, { fontSize: 10.6, color: C.navy, bold: true });
    addBody(s, r[1], 3.05, y + 0.23, 4.45, 0.18, { fontSize: 9.2, color: C.slate });
    addArrow(s, 7.7, y + 0.37, 8.3, y + 0.37, C.teal, 1.0);
    addBody(s, r[2], 8.6, y + 0.23, 3.35, 0.18, { fontSize: 9.2, color: C.teal, bold: true });
  });
  addPill(s, '最终目标：让“真实 score drop”成为可解释、可复现、可规模化积累的数据资产。', 2.08, 6.24, 9.15, C.navy, 'EAF3FA');
  addFooter(s, 17);
}

// 18 Closing
{
  const s = pptx.addSlide(); addBg(s, C.navy);
  s.addShape(pptx.ShapeType.rect, { x: 0, y: 0, w: W, h: 0.13, fill: { color: C.teal }, line: { color: C.teal } });
  s.addText('QUESTION EVOLUTION PIPELINE', { x: 0.78, y: 1.08, w: 5.4, h: 0.25, fontFace: FONT, fontSize: 10, color: '7FD6CA', bold: true, charSpacing: 1.3, margin: 0 });
  s.addText('题目进化的终点，不是\n更复杂的题面。', { x: 0.75, y: 1.75, w: 7.3, h: 1.2, fontFace: FONT, fontSize: 31, color: C.white, bold: true, margin: 0, fit: 'shrink' });
  s.addText('而是可被验证、可被解释、可持续积累的模型能力边界。', { x: 0.78, y: 3.35, w: 7.52, h: 0.35, fontFace: FONT, fontSize: 15, color: 'C7D7E8', margin: 0 });
  s.addShape(pptx.ShapeType.line, { x: 0.78, y: 4.2, w: 1.36, h: 0, line: { color: C.teal, width: 3 } });
  addPill(s, '稳定准入', 0.8, 4.65, 0.9, C.teal, '163C4A');
  addPill(s, '真实评分', 1.88, 4.65, 0.9, C.teal, '163C4A');
  addPill(s, '后验反馈', 2.96, 4.65, 0.9, C.teal, '163C4A');
  addPill(s, '持续进化', 4.04, 4.65, 0.9, C.teal, '163C4A');
  s.addShape(pptx.ShapeType.ellipse, { x: 9.18, y: 1.55, w: 2.44, h: 2.44, fill: { color: '163C4A' }, line: { color: C.teal, width: 1.5 } });
  s.addShape(pptx.ShapeType.ellipse, { x: 9.84, y: 2.22, w: 1.12, h: 1.12, fill: { color: C.teal }, line: { color: C.teal } });
  addArrow(s, 10.38, 4.1, 8.97, 4.95, '7FD6CA', 1.6);
  addBody(s, 'THANK YOU', 0.8, 6.5, 2.2, 0.24, { fontSize: 10, color: '7FD6CA', bold: true });
  s.addText('18', { x: 12.18, y: 7.13, w: 0.45, h: 0.2, fontFace: FONT, fontSize: 8, color: '7FD6CA', bold: true, align: 'right', margin: 0 });
}

const out = 'D:/deep_learning_code/Question_Evolution_Pipeline/V1/Question_Evolution/Question_Evolution_项目全景分析_2026-07-29.pptx';
(async () => {
  await pptx.writeFile({ fileName: out });
  console.log(`Created ${out}`);
})().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
