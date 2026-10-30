import { readFile, writeFile } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

const appRoot = dirname(dirname(fileURLToPath(import.meta.url)));
const library = JSON.parse(
  await readFile(join(appRoot, "..", "data", "library.json"), "utf8"),
);
const names = {
  cet4: "四级阅读与写作",
  cet6: "六级阅读与写作",
  postgrad: "考研阅读与写作",
};
const sources = [];
const curatedProfiles = {
  cet4: {
    teaching_principles: ["先抓文章中心再看细节", "每个答案回原文核对", "写作先保证简单句语法正确"],
    answer_sequence: ["识别题型", "抓中心或关键词", "定位原文", "排除干扰", "安排练习"],
    reading_methods: [
      "先读段落首句和尾句，快速判断文章中心",
      "主旨题优先选择与文章中心最接近的选项",
      "细节题用专有名词、数字和关键词定位",
      "关键词不明显时按题号顺序定位",
      "比较定位句及上下文，利用选项与原文的词汇重合度核对",
    ],
    writing_methods: [
      "不要依赖万能模板",
      "基础薄弱时先保证简单句主谓结构和语法正确",
      "遇到生词时用自己会写的近义词或上位词替代",
      "用主题句组织段落，再逐步增加复杂表达",
      "控制连接词数量，保证逻辑连贯而不是堆砌",
    ],
    correction_style: "先指出一个最关键的问题，再给出学生能独立复现的修改方法。",
    signature: "四级：中心优先、原文定位、简单句先正确。",
  },
  cet6: {
    teaching_principles: ["长篇阅读先筛信息后精读", "写作兼顾准确、连贯和表达变化", "所有判断都要有原文证据"],
    answer_sequence: ["识别题型", "筛选关键词", "核对段意与逻辑", "排除干扰", "升级表达"],
    reading_methods: [
      "先扫描标题、段首和段尾，建立文章结构",
      "长篇阅读先找专有名词、数字和固定概念",
      "细节题按原文顺序定位，减少全文重复阅读",
      "对比定位句和相邻句，警惕偷换概念与过度推断",
    ],
    writing_methods: [
      "先确定中心观点和段落功能，再写句子",
      "避免机械模板，根据题目语境选择表达",
      "保证基础语法正确后再增加复杂句式",
      "用同义替换和核心词复现保持连贯",
      "修改时分别检查内容、逻辑、词汇和语法",
    ],
    correction_style: "同时检查观点是否切题、段落是否连贯、语言是否准确。",
    signature: "六级：结构先行、语境适配、准确优先。",
  },
  postgrad: {
    teaching_principles: ["限时训练与错因分析结合", "在原文中完成精读和标注", "通过重复复习强化句子和论证"],
    answer_sequence: ["限时做题", "逐项分析对错", "精读标注难点", "记忆重点句", "定期复习"],
    reading_methods: [
      "先限时完成，再逐题说明正确项和干扰项的依据",
      "将错误分类为词汇、句意、定位或过度推断",
      "在原文中标注长难句、逻辑转折和生词",
      "把重点句放回上下文理解并记忆",
      "复盘论证结构和作者态度，而不是只记答案",
    ],
    writing_methods: [
      "先搭建论点与论据结构",
      "用主题句明确每段功能",
      "通过句子级练习提升复杂表达准确性",
      "修改时记录反复出现的语法和用词错误",
    ],
    correction_style: "按错误类型逐项定位，保留有效内容后重写关键句。",
    signature: "考研：限时练习、错因分类、精读标注、迭代复习。",
  },
};

for (const [agent, name] of Object.entries(names)) {
  const profile = curatedProfiles[agent];
  const sourceCount = (library.sources || []).filter(source => source.agent === agent).length;
  const cards = [
    {
      text: `阅读方法：${(profile.reading_methods || []).join("；")}`,
    },
    {
      text: `写作方法：${(profile.writing_methods || []).join("；")}`,
    },
    {
      text: `解题顺序：${(profile.answer_sequence || []).join(" → ")}`,
    },
    {
      text: `纠错原则：${profile.correction_style || "先用原文证据核验，再进行表达优化。"}`,
    },
  ];
  sources.push({
    title: `${name}课程方法汇总（${sourceCount}个公开视频）`,
    agent,
    chunks: cards,
  });
}

const publicData = {
  agents: names,
  profiles: curatedProfiles,
  sources,
};
const target = join(appRoot, "public", "course-data.json");
await writeFile(target, JSON.stringify(publicData, null, 2), "utf8");
console.log(`Wrote public method summaries to ${target}`);
