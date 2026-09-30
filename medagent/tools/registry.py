# -*- coding: utf-8 -*-
"""工具注册表:医学工具的 JSON Schema 描述 + 安全执行。

描述按"何时用/何时不该用/参数正例"标准撰写 —— 工具描述是 Agent 行为设计的核心面。
"""
from __future__ import annotations

from typing import Any, Callable

from .medical_tools import (check_drug_interaction, interpret_lab,
                            recommend_department, search_guideline)
from .medical_tools2 import (assess_child_fever, check_adherence,
                             food_guidance, generate_followup_plan,
                             medication_education, symptom_triage_flow)


class ToolRegistry:
    def __init__(self):
        self._tools: dict[str, dict[str, Any]] = {}

    def register(self, name: str, description: str, parameters: dict,
                 fn: Callable[..., Any]) -> None:
        self._tools[name] = {
            "spec": {"type": "function",
                     "function": {"name": name, "description": description,
                                  "parameters": parameters}},
            "fn": fn,
        }

    def specs(self) -> list[dict]:
        return [t["spec"] for t in self._tools.values()]

    def get(self, name: str) -> Callable[..., Any]:
        if name not in self._tools:
            raise KeyError(f"未注册工具: {name}")
        return self._tools[name]["fn"]

    def names(self) -> list[str]:
        return list(self._tools)


def build_registry() -> ToolRegistry:
    reg = ToolRegistry()
    reg.register(
        "interpret_lab",
        ("解读单项检验指标:对照参考区间判断偏高/偏低并给出临床意义。"
         "用户提供了化验数值(如'血红蛋白 95')时使用;item 用标准检验名"
         "(支持:血红蛋白/白细胞/血小板/空腹血糖/糖化血红蛋白/肌钙蛋白I/D-二聚体/"
         "血肌酐/尿酸/丙氨酸氨基转移酶/血钾/中性粒细胞比率)。"
         "只做区间解读,不下诊断。"),
        {"type": "object",
         "properties": {"item": {"type": "string", "description": "标准检验指标名,如 '空腹血糖'"},
                        "value": {"type": "number", "description": "数值,如 7.8"},
                        "patient": {"type": "string", "description": "患者描述,如 '成人男'", "default": "成人"}},
         "required": ["item", "value"]},
        interpret_lab,
    )
    reg.register(
        "check_drug_interaction",
        ("查询两种药物之间的相互作用(严重程度/效应/处理建议)。"
         "用户提及同时使用或打算联用两种药(如'华法林和阿司匹林能一起吃吗')时使用;"
         "参数用通用名。返回仅供参考,调整用药须医师决定。"),
        {"type": "object",
         "properties": {"drug_a": {"type": "string", "description": "药品通用名,如 '华法林'"},
                        "drug_b": {"type": "string", "description": "药品通用名,如 '克拉霉素'"}},
         "required": ["drug_a", "drug_b"]},
        check_drug_interaction,
    )
    reg.register(
        "search_guideline",
        ("检索临床指南知识卡:诊断标准(高血压/糖尿病分级)、鉴别诊断(胸痛/头痛)、"
         "评分工具(CURB-65/CHA2DS2-VASc)、时间窗(溶栓4.5h)、用药原则( Hp四联/抗菌阶梯)。"
         "回答任何'标准是什么/如何鉴别/怎么治'的方法论问题前必须先检索,禁止凭记忆回答。"),
        {"type": "object",
         "properties": {"query": {"type": "string", "description": "自然语言检索词"}},
         "required": ["query"]},
        search_guideline,
    )
    reg.register(
        "recommend_department",
        ("根据症状描述推荐就诊科室,并识别急症信号(命中时返回 urgent=true 与就医指令)。"
         "回答'该挂什么科'时使用;symptoms 传用户原话中的症状片段。"),
        {"type": "object",
         "properties": {"symptoms": {"type": "string", "description": "症状描述,如 '胸痛伴大汗'"}},
         "required": ["symptoms"]},
        recommend_department,
    )
    reg.register(
        "symptom_triage_flow",
        ("按症状自查流程树给出分级建议(emergency/clinic/self_care)。"
         "用户描述某个主症状并想知道'要不要紧/该怎么办'时使用;"
         "symptom 传标准症状词(发热/头痛/腹痛/咳嗽/胸痛/皮疹),"
         "red_flags 传用户描述的伴随情况(如'精神萎靡,3月龄')。"),
        {"type": "object",
         "properties": {"symptom": {"type": "string", "description": "标准症状词,如 '发热'"},
                        "red_flags": {"type": "string", "description": "伴随情况描述", "default": ""}},
         "required": ["symptom"]},
        symptom_triage_flow,
    )
    reg.register(
        "medication_education",
        ("查询常用药品的用药教育:服药要点/注意事项/警示信号。"
         "用户询问'这个药怎么吃/要注意什么'时使用(如二甲双胍/华法林/他汀等常见慢病药)。"
         "返回一般教育信息,不含具体处方剂量。"),
        {"type": "object",
         "properties": {"drug": {"type": "string", "description": "药品名,如 '二甲双胍'"}},
         "required": ["drug"]},
        medication_education,
    )
    reg.register(
        "assess_child_fever",
        ("儿童发热危险分层(high/intermediate/low)。家长描述患儿情况时使用;"
         "description 传原话(如'2岁,发热39度,精神还可以,能吃能玩')。"
         "high 风险返回立即就医指令。"),
        {"type": "object",
         "properties": {"description": {"type": "string", "description": "家长对患儿情况的描述"}},
         "required": ["description"]},
        assess_child_fever,
    )
    reg.register(
        "generate_followup_plan",
        ("生成慢病随访管理计划:达标目标/监测项目与频率/生活方式处方/红旗征/复诊周期。"
         "用户或家属询问'这个病平时怎么管理/多久复查/要注意什么'时使用。"
         "disease 支持:高血压/2型糖尿病/哮喘/慢性肾脏病/房颤(抗凝)。"
         "输出为教育模板,具体以主治医生为准。"),
        {"type": "object",
         "properties": {"disease": {"type": "string", "description": "慢病名,如 '高血压'"}},
         "required": ["disease"]},
        generate_followup_plan,
    )
    reg.register(
        "check_adherence",
        ("慢病用药依从性自查清单。患者说'总是忘记吃药/感觉好了想停药'或需要"
         "评估管理质量时使用;disease 支持:高血压/糖尿病/哮喘/抗凝。"),
        {"type": "object",
         "properties": {"disease": {"type": "string", "description": "慢病名,如 '糖尿病'"}},
         "required": ["disease"]},
        check_adherence,
    )
    reg.register(
        "food_guidance",
        ("食物/用药-饮食安全指导:高钾(肾病)/高嘌呤(痛风)/维生素K(华法林)/"
         "孕期避免/哺乳期注意/空腹伤胃 等。"
         "用户问'吃什么不能吃什么/饮食注意'时使用。"),
        {"type": "object",
         "properties": {"topic": {"type": "string", "description": "主题,如 '高钾食物', '华法林饮食'"}},
         "required": ["topic"]},
        food_guidance,
    )
    return reg
