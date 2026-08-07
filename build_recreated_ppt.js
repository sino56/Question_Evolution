const pptxgen = require('pptxgenjs');

const pptx = new pptxgen();
pptx.defineLayout({ name: 'REF_16_9', width: 13.333333, height: 7.5 });
pptx.layout = 'REF_16_9';
pptx.author = 'Codex';
pptx.subject = 'PPT3 reference recreation';
pptx.title = '复刻版PPT';
pptx.company = 'OpenAI';
pptx.lang = 'zh-CN';
pptx.theme = {
  headFontFace: 'Microsoft YaHei',
  bodyFontFace: 'Microsoft YaHei',
  lang: 'zh-CN'
};

const SW = 13.333333, SH = 7.5, PXW = 592, PXH = 333;
const FONT_SCALE = 1.4;
const X = px => px / PXW * SW;
const Y = px => px / PXH * SH;
const C = {
  ink: '26333B', dark: '17242C', muted: '62737B', line: 'A7B6BC',
  blue: '2D7098', blueFill: 'E8F3FA', blueFill2: 'DDECF7',
  green: '16845C', greenFill: 'E6F6EF',
  orange: 'C67A15', orangeFill: 'FFF5E5',
  red: 'B55742', redFill: 'FBEFEB',
  grayFill: 'F5F7F7', white: 'FFFFFF'
};

function addText(slide, text, x, y, w, h, opt = {}) {
  const base = {
    x: X(x), y: Y(y), w: X(w), h: Y(h),
    fontFace: opt.fontFace || 'Microsoft YaHei',
    fontSize: (opt.fontSize || 11.5) * FONT_SCALE,
    color: opt.color || C.ink,
    bold: !!opt.bold,
    margin: opt.margin === undefined ? 0 : opt.margin,
    breakLine: false,
    valign: opt.valign || 'mid',
    align: opt.align || 'left',
    paraSpaceAfterPt: opt.paraSpaceAfterPt || 0,
    lineSpacingMultiple: opt.lineSpacingMultiple,
    isTextBox: true,
    fit: 'shrink'
  };
  if (opt.bullet) base.bullet = { type: 'bullet' };
  if (opt.rotate !== undefined) base.rotate = opt.rotate;
  slide.addText(text, base);
}

function addBox(slide, x, y, w, h, opt = {}) {
  const type = opt.type || pptx.ShapeType.roundRect;
  slide.addShape(type, {
    x: X(x), y: Y(y), w: X(w), h: Y(h),
    rectRadius: opt.radius,
    fill: { color: opt.fill || C.white, transparency: opt.transparency || 0 },
    line: { color: opt.line || C.line, width: opt.lineWidth || 0.9, dash: opt.dash },
    shadow: opt.shadow ? { type: 'outer', color: 'B8C0C3', blur: 1.5, angle: 45, distance: 1, opacity: 0.18 } : undefined
  });
  if (opt.text !== undefined) {
    addText(slide, opt.text, x + (opt.padX || 3), y + (opt.padY || 0), w - 2 * (opt.padX || 3), h, {
      fontSize: opt.fontSize || 10.5, bold: opt.bold, color: opt.color || C.ink,
      align: opt.align || 'center', valign: opt.valign || 'mid', margin: 0
    });
  }
}

function addLine(slide, x1, y1, x2, y2, opt = {}) {
  const left = Math.min(x1, x2), top = Math.min(y1, y2);
  const width = Math.abs(x2 - x1), height = Math.abs(y2 - y1);
  slide.addShape(pptx.ShapeType.line, {
    x: X(left), y: Y(top), w: X(width), h: Y(height),
    flipH: x2 < x1,
    flipV: y2 < y1,
    line: {
      color: opt.color || C.line, width: opt.width || 1,
      dash: opt.dash, beginArrowType: opt.beginArrowType,
      endArrowType: opt.endArrowType
    }
  });
}

function arrow(slide, x1, y1, x2, y2, opt = {}) {
  addLine(slide, x1, y1, x2, y2, { ...opt, endArrowType: opt.endArrowType || 'triangle' });
}

function bulletList(slide, items, x, y, w, lineH = 18, opt = {}) {
  items.forEach((t, i) => {
    slide.addShape(pptx.ShapeType.ellipse, {
      x: X(x), y: Y(y + i * lineH + 6), w: X(4), h: Y(4),
      fill: { color: opt.bulletColor || '2D78AE' }, line: { color: opt.bulletColor || '2D78AE', transparency: 100 }
    });
    addText(slide, t, x + 10, y + i * lineH, w - 10, lineH, {
      fontSize: opt.fontSize || 10.2, bold: opt.bold, color: opt.color || C.ink, valign: 'mid'
    });
  });
}

function pageNumber(slide, n) {
  addBox(slide, 8, 303, 35, 23, { fill: '363636', line: '363636', type: pptx.ShapeType.roundRect, lineWidth: 0.1 });
  addText(slide, String(n), 9, 303, 33, 23, { fontFace: 'Arial', fontSize: 15, color: C.white, align: 'center', bold: false });
}

function baseSlide(n) {
  const s = pptx.addSlide();
  s.background = { color: 'FDFEFE' };
  addLine(s, 0, 2, 592, 2, { color: 'CFD6D8', width: 0.6 });
  pageNumber(s, n);
  return s;
}

function titleBlock(s, title, subtitle, y = 6, lineY = 39) {
  addText(s, title, 7, y, 540, 21, { fontSize: 15.5, bold: true, color: C.dark, valign: 'mid' });
  if (subtitle) addText(s, subtitle, 7, y + 19, 540, 14, { fontSize: 8.6, color: C.muted });
  if (lineY !== null) addLine(s, 7, lineY, 549, lineY, { color: '91A0A6', width: 1 });
}

function stepBox(s, x, y, w, h, text, style = 'blue', fs = 10.5) {
  const map = {
    blue: [C.blueFill, C.blue], green: [C.greenFill, C.green], orange: [C.orangeFill, C.orange],
    red: [C.redFill, C.red], gray: [C.grayFill, C.muted], white: [C.white, C.ink]
  };
  const [fill, color] = map[style];
  addBox(s, x, y, w, h, { fill, line: 'AAB8BE', shadow: true, text, fontSize: fs, bold: true, color });
}

function slide1() {
  const s = baseSlide(1);
  addText(s, 'QA Evolution Agent完全体设计原理', 39, 33, 430, 34, { fontFace: 'Times New Roman', fontSize: 23.5, bold: true, color: '17242C' });
  addText(s, '受控实验 Agent Harness：已实施路径 + 目标态治理闭环', 40, 84, 330, 20, { fontSize: 10.5, bold: true, color: '265C78' });
  const nodes = [
    [37, 148, 55, 21, '目标', 'blue'], [111, 148, 55, 21, '计划', 'green'],
    [185, 148, 55, 21, '执行', 'orange'], [260, 148, 55, 21, '观察', 'green'],
    [334, 148, 55, 21, '治理', 'red']
  ];
  nodes.forEach((n, i) => { stepBox(s, ...n, 11); if (i < nodes.length - 1) arrow(s, n[0] + n[2] + 2, 158.5, nodes[i + 1][0] - 4, 158.5, { color: '8CA0A8', width: 1.1 }); });
  addBox(s, 36, 214, 434, 23, { fill: 'F8FBFC', line: '9FB6BE', lineWidth: 1, shadow: true });
  addText(s, '核心：Agent 不替代业务流水线，而是把实验控制变得可计划、可恢复、可审计。', 40, 215, 425, 21, { fontSize: 10.8, bold: true, color: '274E62' });
  // Decorative concentric ring at the right.
  [[489,188,88,'D9D9D9'],[498,197,70,'E6E6E6'],[507,206,52,'D2D2D2'],[514,213,38,'FAFAFA']].forEach(r => {
    s.addShape(pptx.ShapeType.ellipse, { x:X(r[0]), y:Y(r[1]), w:X(r[2]), h:Y(r[2]), fill:{color:r[3]}, line:{color:r[3], transparency:100} });
  });
}

function slide2() {
  const s = baseSlide(2);
  titleBlock(s, '设计定位：Agent 是控制器', '在问题进化系统外编排立项规划', 7, 40);
  addBox(s, 5, 46, 149, 102, { fill:'FFFFFF', line:C.blue, dash:'dash', lineWidth:1.2 });
  addBox(s, 416, 45, 165, 103, { fill:'FFFFFF', line:C.blue, dash:'dash', lineWidth:1.2 });
  addText(s, 'Agent 控制器', 39, 48, 84, 17, { fontSize:9, bold:true, color:C.blue, align:'center' });
  addText(s, 'Question Evolution 执行层', 437, 48, 126, 17, { fontSize:8.6, bold:true, color:C.green, align:'center' });
  bulletList(s, ['把用户目标转为 AgentTask','生成可审计 AgentPlan','检查工具、环境、预算和权限','观察实验产物并输出决策'], 14, 68, 135, 19, {fontSize:9.3});
  bulletList(s, ['样本画像与算子路由','候选题生成与校验','作答、Rubric、真实评分','效果分析、状态和 Memory 更新'], 425, 68, 150, 19, {fontSize:8.9});
  addBox(s, 169, 73, 228, 49, { fill:C.blueFill2, line:'B4C8D4', lineWidth:0.9, shadow:true });
  addText(s, '两层之间只通过\n工具契约 + 观察层 + 实验结果记录\n进行交互', 185, 76, 196, 42, { fontSize:10.2, bold:true, color:'315B73', align:'center', valign:'mid' });
  arrow(s, 155, 96, 168, 96, { color:C.blue, width:1.6 });
  arrow(s, 415, 96, 398, 96, { color:C.blue, width:1.6 });

  addText(s, '总体架构：两层闭环', 7, 160, 230, 19, {fontSize:13, bold:true, color:C.dark});
  addText(s, '上层控制执行节奏，下层完成题目进化和评分闭环', 8, 178, 310, 14, {fontSize:8.4, color:C.muted});
  addLine(s, 7, 193, 550, 193, {color:'95A3A8', width:1});
  addText(s, 'Agent Harness 控制层', 9, 207, 145, 15, {fontFace:'Times New Roman', fontSize:9.5, bold:true, color:C.blue});
  const top = [['AgentTask',7,226,59],['Context\nBuilder',75,224,53],['Planner',142,225,55],['Policy\nGuard',210,224,54],['Tool\nRunner',276,224,54],['Observer',343,225,55],['Decision',408,225,55],['Reporter',473,225,54]];
  top.forEach((v,i)=>{stepBox(s,v[1],v[2],v[3],25,v[0],'blue',8.5); if(i<top.length-1) arrow(s,v[1]+v[3]+1,237,top[i+1][1]-2,237,{color:'7895A4',width:1});});
  addText(s, '必要时重规划，重新调整预算', 234, 198, 210, 16, {fontSize:9.3,bold:true,color:C.orange,align:'center'});
  addLine(s,171,224,171,214,{color:C.blue,width:1.1}); addLine(s,171,214,434,214,{color:C.blue,width:1.1}); addLine(s,434,214,434,224,{color:C.blue,width:1.1});
  addText(s, '下一步决策', 408, 251, 60, 15, {fontSize:8.3, color:C.dark});
  addText(s, '人工审计', 477, 251, 55, 15, {fontSize:8.3, color:C.dark});
  addBox(s, 272, 261, 161, 28, {fill:'F7F8F8',line:'C5CCCE',shadow:true});
  addText(s, '工具执行产生实验产物\n观察层汇总为可决策状态', 279,262,147,25,{fontSize:8.6,bold:true,align:'center'});
  addText(s, 'Question Evolution 执行层', 8, 286, 160, 14, {fontFace:'Times New Roman',fontSize:9.2,bold:true,color:C.green});
  const bot=[['画像',8,300,59],['路由',71,300,59],['生成',134,300,59],['校验',197,300,59],['答案',260,300,59],['Rubric',323,300,59],['评分',386,300,59],['状态',449,300,38]];
  bot.forEach((v,i)=>{stepBox(s,v[1],v[2],v[3],20,v[0],'green',8.2); if(i<bot.length-1) arrow(s,v[1]+v[3]+1,310,bot[i+1][1]-2,310,{color:'7BAF97',width:0.9});});
  addBox(s, 4, 295, 493, 31, {fill:'FFFFFF',transparency:100,line:C.green,dash:'dash',lineWidth:1.1});
}

function slide3() {
  const s=baseSlide(3); titleBlock(s,'运行主链：从任务到报告','支持恢复和复盘',7,39);
  const top=[['1','解析 AgentTask\ngoal / input_file / budget',13,47,122],['2','创建运行目录\nagent_runs/<date>/<id>',151,47,122],['3','生成计划\nagent_plan.json',289,47,121],['4','构建上下文\ncontext_pack.json',427,47,121]];
  const bot=[['8','输出报告\nagent_report.md',13,99,122],['7','写入决策\nagent_decisions.jsonl',151,99,122],['6','观察产物\nagent_observation.json',289,99,121],['5','调用工具\ncheck / run / resume',427,99,121]];
  [...top,...bot].forEach(v=>{addBox(s,v[2]+18,v[3],v[4]-18,34,{fill:'F8FAFA',line:'B4C0C4',shadow:true}); addText(s,v[1],v[2]+22,v[3]+1,v[4]-26,32,{fontSize:8.3,bold:true,align:'center'}); addBox(s,v[2],v[3],16,16,{fill:'F5FAFC',line:'8FAFBE',text:v[0],fontSize:8.5,bold:true,color:C.blue,padX:0});});
  for(let i=0;i<3;i++) arrow(s,top[i][2]+top[i][4]+1,64,top[i+1][2]-2,64,{color:'8AA2AE'});
  arrow(s,549,82,549,96,{color:'8AA2AE'});
  for(let i=3;i>0;i--) arrow(s,bot[i][2]-2,116,bot[i-1][2]+bot[i-1][4]+1,116,{color:'8AA2AE'});
  addBox(s,13,137,535,17,{fill:C.blueFill,line:'B9CBD4',text:'状态、事件、观察、决策、报告分开保存：既能回放发生了什么，也能定位下一次从哪里恢复。',fontSize:8.9,bold:true,color:'365E75'});
  addText(s,'计划生成：选择搜索模式',7,158,290,21,{fontSize:13.5,bold:true,color:C.dark});
  addText(s,'Planner 输出结构化 step：工具、参数、前置条件、预期产物、成功条件、失败处理和预算，便于后续可恢复',8,177,540,13,{fontSize:7.8,color:C.muted});
  addLine(s,7,189,548,189,{color:'95A3A8',width:1});
  const rows=[
    ['目标包含：边界 / 多算子 / 比较','multi_operator_branch','green'],
    ['目标包含：组合 / 叠加 / 二次 / vertical','multi_operator_vertical_stack','green'],
    ['目标包含：传统 / 逐轮 / 主链','single_branch','green'],
    ['只读检查已有实验','report_only review','orange']
  ];
  rows.forEach((r,i)=>{const yy=196+i*28; addBox(s,43,yy,220,21,{fill:'FFFFFF',line:'B9C2C5',shadow:true,text:r[0],fontSize:8.9,bold:true,align:'left',padX:7}); arrow(s,265,yy+10.5,307,yy+10.5,{color:'7D929B'}); stepBox(s,309,yy,190,21,r[1],r[2],9.2);});
  addBox(s,56,309,427,18,{fill:C.blueFill,line:'BBCBD3',text:'无法从目标判断时：默认 multi_operator_branch，并在记录中写明',fontSize:8.2,bold:true,color:'356078'});
}

function slide4(){
  const s=baseSlide(4); titleBlock(s,'治理边界：Policy Guard 防止越权','计划必须显式工具、环境、预算和报告约束',7,38);
  addBox(s,4,42,124,18,{fill:'F8FCF9',line:'8CAB97',text:'允许',fontSize:9,bold:true,color:C.green});
  addBox(s,321,42,124,18,{fill:'FFFBF7',line:'AA9686',text:'禁止',fontSize:9,bold:true,color:C.red});
  bulletList(s,['登记工具：check / run / resume / observe / decide / report','白名单环境变量：input_file、search_mode、boundary_target等','业务失败按业务路径处理：score_increased、not_applicable、\nvalidation_failed'],7,65,282,20,{fontSize:8.7});
  bulletList(s,['自动修改 Prompt、Router、Rubric、算子文件或 Schema','发布 active 全局 Memory，或让 Memory 越过 Router','把自动评分写成“已确认有效边界”','用文件存在替代 manifest 完整性判断'],323,65,255,20,{fontSize:8.5});
  addBox(s,62,136,420,18,{fill:C.orangeFill,line:'DCC9A8',text:'核心原则：系统失败和业务失败分流；真实评分只是边界候选证据，需要人工确认',fontSize:8.7,bold:true,color:C.orange});
  addText(s,'工具执行：统一封装现有入口',7,164,300,21,{fontSize:13.5,bold:true,color:C.dark});
  addText(s,'Agent 不重新实现编排逻辑，只调用现有工具',8,183,300,13,{fontSize:8.2,color:C.muted});
  addLine(s,7,196,549,196,{color:'95A3A8',width:1});
  const cols=[
    [7,'check_environment','检查 Bash、输入样本、依赖和 API 配置','observe_experiment','读取 summary、manifest、statistics\n和 JSONL','blue'],
    [183,'run_full_loop','调用 run_loop.sh，启动完整实验','decide_next_action','根据 Observation 输出停止、阻塞或\n重试决策','green'],
    [361,'resume_full_loop','调用 resume_run_loop.sh，从断点续跑','write_agent_report','写出可审计 Markdown 报告','orange']
  ];
  cols.forEach(c=>{stepBox(s,c[0],201,151,18,c[1],c[6],8.6); addText(s,c[2],c[0]+2,222,153,25,{fontSize:8.3,bold:true}); stepBox(s,c[0]+24,247,110,17,c[3],'gray',7.9); addText(s,c[4],c[0]+2,266,153,30,{fontSize:8.3,bold:true,align:'center'});});
  addBox(s,77,294,429,32,{fill:C.blueFill2,line:'B8C9D3',shadow:true});
  addText(s,'ToolResult 统一记录工具调用\nEventLogger 对工具开始、完成、失败和决策全部追加写入 agent_events.jsonl',86,296,410,28,{fontSize:8.1,bold:true,align:'center',color:'315D75'});
}

function slide5(){
  const s=baseSlide(5); titleBlock(s,'记忆系统','法入分层：完整 JSONL、完整时间回放和简单统计不混长期记忆，可进失败模式和人工编辑结论才进入候选',6,39);
  const rows=[
    ['L0','运行记忆','本次 Agent 如何计划、执行、观察和停止','不在线影响','gray'],
    ['L1','实验事实','当前实验发生了什么，含失败和候选事实','不在线影响','gray'],
    ['L2','全局基本','多个实验提交的可追溯事实','不在线影响','gray'],
    ['L3','预告卡','超级、场景、算子组合的有效或高风险规律','只读摘要','green'],
    ['L4','程序规则','写入、发布、删除、回滚和门槛规则','规则引擎','green']
  ];
  rows.forEach((r,i)=>{const yy=44+i*23; addBox(s,4,yy,30,18,{fill:i<3?'F3F7F9':'EEF7F5',line:'B8C4C9',text:r[0],fontSize:8.8,bold:true,color:i<3?C.blue:C.green,padX:0}); addBox(s,40,yy,82,18,{fill:'F7F9FA',line:'C1CACD',text:r[1],fontSize:8.4,bold:true}); addText(s,r[2],134,yy,275,18,{fontSize:8.7,bold:true}); addBox(s,414,yy,89,18,{fill:r[4]==='green'?C.greenFill:'F3F5F6',line:'BFC8CB',text:r[3],fontSize:8.3,bold:true,color:r[4]==='green'?C.green:C.muted});});
  addText(s,'审计与回放：长期运行的可信基础',7,169,350,21,{fontSize:13.5,bold:true,color:C.dark});
  addText(s,'保证能复盘、能追溯问题',8,188,280,13,{fontSize:8.2,color:C.muted});
  addLine(s,7,199,549,199,{color:'95A3A8',width:1});
  stepBox(s,36,205,214,19,'Trace','blue',10); stepBox(s,304,205,215,19,'Checkpoint','green',10);
  addText(s,'目标、计划版本、工具调用、Observation、判断、终止回退',18,230,250,18,{fontSize:8.5,bold:true,align:'center'});
  addText(s,'对应 Step、输入快照、输出产物、manifest、恢复限制',286,230,253,18,{fontSize:8.5,bold:true,align:'center'});
  stepBox(s,36,253,214,19,'Replay','orange',10); stepBox(s,304,253,215,19,'Metrics','green',10);
  addText(s,'回放 Trace + Snapshot 验证结果，不改写历史结果',18,278,250,18,{fontSize:8.5,bold:true,align:'center'});
  addText(s,'边界候选率、放弃率、实验量、Judge 分析、成本',286,278,253,18,{fontSize:8.5,bold:true,align:'center'});
  const tags=['总体状态','最后操作回回','分歧率','算子路径','Judge稳定性','引用的记忆验证\n和数据','结束原因'];
  let tx=7; tags.forEach((t,i)=>{const ww=[75,96,66,82,78,105,68][i]; addBox(s,tx,305,ww,21,{fill:i%3===1?C.orangeFill:'F7F8F8',line:'BCC5C8',text:t,fontSize:7.7,bold:true}); tx+=ww+6;});
}

function slide6(){
  const s=baseSlide(6); titleBlock(s,'多 Agent 协作：主控执行，专项只建议','适合复盘、记忆编辑、计划候选和人工复核代理',7,40);
  stepBox(s,221,48,123,23,'主控 Agent','blue',10.2);
  addText(s,'观察编',247,77,70,13,{fontSize:8.2,bold:true,color:C.green,align:'center'});
  stepBox(s,220,84,125,28,'生成 observations','green',9.2);
  addText(s,'协调器',247,113,70,13,{fontSize:8.2,bold:true,color:C.green,align:'center'});
  stepBox(s,204,122,158,28,'分发任务 / 收集合成','green',9.2);
  arrow(s,282,71,282,83,{color:'7A9890'}); arrow(s,282,111,282,121,{color:'7A9890'});
  const diags=[['路由诊断',23,182,82],['生成诊断',130,181,82],['校验诊断',237,181,82],['评分稳定性',345,181,84],['搜索成本',454,181,78]];
  diags.forEach(d=>addBox(s,d[1],d[2],d[3],20,{fill:'F9FBFB',line:'B5BEC2',shadow:true,text:d[0],fontSize:8.7,bold:true}));
  diags.forEach(d=>addLine(s,282,151,d[1]+d[3]/2,d[2],{color:'515C61',width:1.1}));
  addBox(s,7,220,244,111,{fill:'F8FAFB',line:'B9C5CB',shadow:true});
  addText(s,'统一输入：',15,228,90,18,{fontSize:9.4,bold:true});
  bulletList(s,['每轮后的 evidence_pack.json，不含密钥、完整环境变量或完整控制器产物','协调器先查 advisor_registry.py；','每个专项 Agent 都必须提供登记；','每次根据观察条件选择 Agent；'],15,248,228,20,{fontSize:7.9,bulletColor:C.dark});
  addBox(s,276,220,288,111,{fill:C.orangeFill,line:'E1CDA9',shadow:true});
  addText(s,'统一输出：',285,228,90,18,{fontSize:9.4,bold:true,color:C.orange});
  bulletList(s,['advisor_advice.json：超权建议拒绝，无证据建议仍为人工复核；','高风险建议优先进入人工复核；','多个 Agent 冲突时写入冲突清单；','只有证据充分、权限合法、不冲突的建议才进入 proposal；'],285,248,268,19,{fontSize:7.8,bulletColor:C.orange,color:C.orange});
}

function slide7(){
  const s=baseSlide(7); titleBlock(s,'Skills 体系：稳定操作规范','介入 Agent 在重复场景下的操作规范',6,39);
  stepBox(s,7,43,128,26,'Tool\n真实执行动作','blue',9.2);
  stepBox(s,198,43,133,26,'Operator\n业务进化算子','green',9.2);
  stepBox(s,392,43,133,26,'Skill\n可复用工作规程','orange',9.2);
  addText(s,'实验重启 / 检查 / 恢复诊断',7,73,155,14,{fontSize:8.5,bold:true});
  addText(s,'记忆编辑 / 策略建议 / 算子诊断 / 人工审核',183,73,198,14,{fontSize:8.1,bold:true});
  addText(s,'计划调整 / 多 Agent Advice / 模型路由',392,73,178,14,{fontSize:8.1,bold:true});
  addText(s,'- experiment-review-skill\n- environment-check-skill\n- recovery-diagnosis-skill',10,88,156,54,{fontSize:8.1,bold:true,valign:'top'});
  addText(s,'- memory-compile-skill\n- strategy-proposal-skill\n- operator-diagnosis-skill\n- human-review-precheck-skill',183,88,196,67,{fontSize:8.1,bold:true,valign:'top'});
  addText(s,'- planning-strategy-skill\n- multi-agent-advisor-skill\n- model-routing-skill',392,88,180,54,{fontSize:8.1,bold:true,valign:'top'});
  addText(s,'上下文管理：稳定内容前置，动态信息后置',7,144,400,20,{fontSize:13.1,bold:true,color:C.dark});
  addText(s,'目标是减少重复预设上下文、同时保证证据和过程可恢复',8,163,400,13,{fontSize:8.2,color:C.muted});
  addLine(s,7,176,549,176,{color:'95A3A8',width:1});
  const rows=[
    ['稳定前置','角色、硬约束、输出 schema、工具说明、Skill 规程','强缓存','blue'],
    ['快照前置','Policy、Prompt、Operator、Memory、Tool、Skill 版本','随快照失效','green'],
    ['任务上下文','目标、搜索模式、执行范围、预算、允许工具','弱缓存','orange'],
    ['动态尾部','运行目录、当前步骤、最新观察、错误摘要、恢复点','不缓存','green']
  ];
  rows.forEach((r,i)=>{const yy=181+i*29; stepBox(s,8,yy,91,18,r[0],r[3],8.5); addText(s,r[1],112,yy,300,18,{fontSize:8.5,bold:true}); stepBox(s,426,yy,80,18,r[2],i===0?'blue':i===1?'green':i===2?'orange':'green',8.2);});
  addBox(s,17,304,530,22,{fill:C.blueFill,line:'BCD0DA',text:'只缓存全文字内容：schema、模板、Skill、Tool、Policy、Prompt、Operator、Memory 与得分当前版本。',fontSize:8.1,bold:true,color:'365F75'});
}

function slide8(){
  const s=baseSlide(8); titleBlock(s,'Global Judge：自进化治理','负责复盘和提出修改建议，不参与当前实时正式决策',7,39);
  const flow=[['Evidence Pack',5,43,80,'blue'],['诊断层级',92,43,79,'blue'],['优化策略',178,43,79,'orange'],['人工审核',264,43,79,'orange'],['Replay /\nHoldout',357,43,80,'green'],['记忆结果',445,43,72,'green']];
  flow.forEach((v,i)=>{stepBox(s,v[1],v[2],v[3],22,v[0],v[4],8.5); if(i<flow.length-1) arrow(s,v[1]+v[3]+1,54,flow[i+1][1]-2,54,{color:'8FA0A6'});});
  addText(s,'备项实验记录',7,69,150,13,{fontSize:8.5,bold:true});
  addBox(s,6,82,173,18,{fill:'F9FCFD',line:'AEC0C8',text:'诊断必须落层级',fontSize:8.5,bold:true,color:C.blue});
  bulletList(s,['样本情况','路由描述','算子选择','验证结果','rubric/judge结果','记忆','搜索成本以及预算'],7,106,177,18,{fontSize:8.6});
  addBox(s,289,82,173,18,{fill:'FFFDFC',line:'D4C3A6',text:'Proposal 生成周期',fontSize:8.5,bold:true,color:C.orange});
  bulletList(s,['适合验证','人工复核','进一步评估','证据不足则拒绝','正式生效'],290,106,170,18,{fontSize:8.6});
  addBox(s,92,215,305,24,{fill:C.redFill,line:'E0BEB1',text:'初版 Global Judge 分析结果全部需要人工审核后再实验具体改进计划',fontSize:8.4,bold:true,color:C.red});
}

function slide9(){
  const s=baseSlide(9); titleBlock(s,'动态预算：把剩余资源转向高收益路径','Agent 可以因分配未知任务，但不能突破硬预算或改写历史消耗',7,39);
  const flow=[['Observation',6,46,79,'blue'],['预算建议',92,46,72,'blue'],['Budget\nValidator',172,46,82,'orange'],['Replan',269,46,74,'orange'],['Executor',358,46,73,'green'],['Budget Ledger',446,46,74,'green']];
  flow.forEach((v,i)=>{stepBox(s,v[1],v[2],v[3],23,v[0],v[4],8.1); if(i<flow.length-1) arrow(s,v[1]+v[3]+1,57.5,flow[i+1][1]-2,57.5,{color:'8FA0A6'});});
  addBox(s,16,85,146,19,{fill:'FFF9F7',line:'D2C5C1',text:'可以减少',fontSize:9,bold:true,color:C.red});
  addBox(s,191,85,145,19,{fill:'F7FCF9',line:'BACCC1',text:'可以增加',fontSize:9,bold:true,color:C.green});
  addBox(s,365,85,146,19,{fill:'F8FAFA',line:'C2C9CB',text:'必须拒绝',fontSize:9,bold:true,color:C.muted});
  bulletList(s,['连续 validation_failed','连续 not_applicable','高成本无收益','重复题面过多'],18,114,145,20,{fontSize:8.6});
  bulletList(s,['出现 score_decreased','接近边界且机制清楚','评分分歧需要评估','一层有效再深二层'],193,114,146,20,{fontSize:8.6});
  bulletList(s,['突破硬预算','改写历史消耗','建议伪写评分','快照不一致继续'],367,114,146,20,{fontSize:8.6});
}

function slide10(){
  const s=baseSlide(10); titleBlock(s,'实施路线：从可运行到可治理','先稳定单 Agent 控制面，再建设长期记忆、高链治理和专项协作',7,39);
  const row1=[['1','Agent 外壳',23,45,78,'green'],['2','Session /\nPlanner',132,45,77,'blue'],['3','Tool /\nObservation',239,45,78,'orange'],['4','Memory 五层',348,45,74,'gray'],['5','Global Judge',455,45,76,'gray']];
  const row2=[['6','Replay / Audit',23,116,78,'gray'],['7','Multi-Agent',132,116,77,'gray'],['8','Skills',239,116,78,'gray'],['9','Context Cache',348,116,78,'gray'],['10','Budget Realloc',455,116,76,'gray']];
  [...row1,...row2].forEach(v=>{addBox(s,v[2],v[3],v[4],28,{fill:v[5]==='green'?C.greenFill:v[5]==='blue'?C.blueFill:v[5]==='orange'?C.orangeFill:'F4F6F7',line:'B5C0C4',shadow:true,text:v[1],fontSize:8.7,bold:true,color:v[5]==='green'?C.green:v[5]==='blue'?C.blue:v[5]==='orange'?C.orange:C.muted}); addBox(s,v[2]-19,v[3]+2,17,16,{fill:'F8FAFA',line:'A7B3B7',text:v[0],fontSize:8.1,bold:true,padX:0});});
  for(let i=0;i<4;i++) arrow(s,row1[i][2]+row1[i][4]+2,59,row1[i+1][2]-20,59,{color:'8EA0A8'});
  for(let i=0;i<4;i++) arrow(s,row2[i][2]+row2[i][4]+2,130,row2[i+1][2]-20,130,{color:'8EA0A8'});
  const status1=[['已实施',41,'green'],['已实施基础',142,'blue'],['标准化增强',249,'orange'],['目标态',366,'gray'],['目标态',472,'gray']];
  const status2=[['目标态',43],['目标态',151],['目标态',260],['目标态',366],['目标态',472]];
  status1.forEach(v=>addText(s,v[0],v[1],78,74,14,{fontSize:8.1,bold:true,color:v[2]==='green'?C.green:v[2]==='blue'?C.blue:v[2]==='orange'?C.orange:C.muted,align:'center'}));
  status2.forEach(v=>addText(s,v[0],v[1],149,64,14,{fontSize:8.1,bold:true,color:C.muted,align:'center'}));
  addBox(s,32,172,486,25,{fill:C.blueFill,line:'BDCDD5',text:'当前可展示：阶段 1 与阶段 2 基础已经进入代码；阶段 3-10 是完全体目标态和后续优化路径。',fontSize:8.8,bold:true,color:'365F76'});
  bulletList(s,['短期：补齐 Tool Registry、Executor、Observation 类型化。','中期：建设长期 Memory、Global Judge、Replay 和最终输出契约。','长期：引入只读专项 Agent、Skills、上下文缓存和动态预算。'],68,208,475,22,{fontSize:8.9});
}

function slide11(){
  const s=baseSlide(11); titleBlock(s,'底层进化流水线：真实评分闭环','Agent 控制全局，候选题决策先应由真实计算和评分分反馈',7,39);
  const flow=[['Seed',4,45,47,'blue'],['Baseline',58,45,49,'blue'],['画像',114,45,47,'blue'],['路由',168,45,47,'blue'],['生成',222,45,47,'green'],['校验',276,45,47,'green'],['答案',330,45,47,'green'],['Rubric',384,45,47,'orange'],['评分',438,45,47,'orange'],['父子比较',492,45,55,'orange']];
  flow.forEach((v,i)=>{stepBox(s,v[1],v[2],v[3],23,v[0],v[4],8.0); if(i<flow.length-1) arrow(s,v[1]+v[3]+1,56.5,flow[i+1][1]-2,56.5,{color:'8EA0A7',width:0.8});});
  const rows=[
    ['multi_operator_branch','同一父题多算子横向尝试，直接比较父子题'],
    ['multi_operator_vertical_stack','降分节点重新画像路由，扩展两算子路径'],
    ['single_branch','传统主链逐轮迭代，只保留一个候选']
  ];
  rows.forEach((r,i)=>{const yy=80+i*32; addBox(s,4,yy,158,22,{fill:'F8FAFA',line:'B7C1C5',shadow:true,text:r[0],fontSize:8.1,bold:true,color:'3D5968'}); addText(s,r[1],172,yy,355,22,{fontSize:8.9,bold:true});});
  addBox(s,4,178,476,23,{fill:'F4F8FA',line:'B8C7CE',shadow:true,text:'所有分支都必须带真实答案、Rubric 和评分，不能用前置校验替代真实反馈。',fontSize:9.5,bold:true,color:'354C58'});
}

slide1(); slide2(); slide3(); slide4(); slide5(); slide6(); slide7(); slide8(); slide9(); slide10(); slide11();

(async () => {
  await pptx.writeFile({ fileName: 'QA Evolution Agent完全体设计原理.pptx' });
})().catch(err => {
  console.error(err);
  process.exitCode = 1;
});
