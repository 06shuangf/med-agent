# -*- coding: utf-8 -*-
"""医学工具层:确定性 mock 数据源(评测可复现,接口对齐真实医疗系统)。

生产替换映射:
  interpret_lab       -> LIS 检验系统
  check_drug_interaction -> 合理用药系统(如 PASS)
  search_guideline    -> 临床指南知识库(国家指南库/UpToDate)
  recommend_department -> 分诊系统(HIS)
"""
from __future__ import annotations

from typing import Any

# 参考区间(教学级常用值)
LAB_REFERENCE: dict[str, dict[str, Any]] = {
    "血红蛋白": {"unit": "g/L", "low": 120, "high": 160, "sex": "男", "meaning": "贫血<120(女<110);升高见于脱水/真性红细胞增多"},
    "白细胞": {"unit": "×10⁹/L", "low": 3.5, "high": 9.5, "meaning": "升高常见于细菌感染/应激;降低见于病毒感染/药物抑制"},
    "中性粒细胞比率": {"unit": "%", "low": 40, "high": 75, "meaning": "细菌感染常升高;病毒感染常降低伴淋巴比率升高"},
    "血小板": {"unit": "×10⁹/L", "low": 125, "high": 350, "meaning": "<100 出血风险增加;<30 严重,须血液科急会诊"},
    "空腹血糖": {"unit": "mmol/L", "low": 3.9, "high": 6.1, "meaning": "≥7.0 达糖尿病标准(需复测确认);6.1-7.0 空腹受损"},
    "糖化血红蛋白": {"unit": "%", "low": 4.0, "high": 6.0, "meaning": "≥6.5 支持糖尿病诊断;反映近 8-12 周平均血糖"},
    "肌钙蛋白I": {"unit": "ng/mL", "low": 0, "high": 0.04, "meaning": "升高提示心肌损伤,动态观察更重要;结合心电图鉴别 ACS"},
    "D-二聚体": {"unit": "mg/L", "low": 0, "high": 0.5, "meaning": "阴性可低概率排除肺栓塞(低危);升高无特异性"},
    "血肌酐": {"unit": "μmol/L", "low": 57, "high": 97, "meaning": "升高提示肾功能受损;需结合 eGFR 分期"},
    "尿酸": {"unit": "μmol/L", "low": 208, "high": 428, "meaning": "升高:高尿酸血症;>420 持续+症状考虑药物治疗"},
    "丙氨酸氨基转移酶": {"unit": "U/L", "low": 9, "high": 50, "meaning": "升高提示肝细胞损伤;>2倍上限有临床意义"},
    "血钾": {"unit": "mmol/L", "low": 3.5, "high": 5.1, "meaning": "<3.5 低钾(乏力/心律失常);>5.5 高钾急症,须紧急处理"},
    "钠": {"unit": "mmol/L", "low": 135, "high": 145, "meaning": "<135 低钠(乏力/恶心,纠正不宜过快);>145 高钠常伴脱水"},
    "铁蛋白": {"unit": "μg/L", "low": 30, "high": 400, "meaning": "<30 支持缺铁性贫血;炎症时假性升高,需结合 CRP"},
    "C反应蛋白": {"unit": "mg/L", "low": 0, "high": 10, "meaning": "升高提示炎症/感染,细菌感染常显著升高;动态变化有价值"},
    "降钙素原": {"unit": "ng/mL", "low": 0, "high": 0.05, "meaning": "升高倾向细菌感染,指导抗菌启动,动态监测"},
    "糖化血红蛋白": {"unit": "%", "low": 4.0, "high": 6.0, "meaning": "≥6.5 支持糖尿病;反映近 8-12 周平均血糖"},
    "白蛋白": {"unit": "g/L", "low": 40, "high": 55, "meaning": "降低:肝病/肾病综合征/营养不良/慢性病"},
    "总胆红素": {"unit": "μmol/L", "low": 3.4, "high": 17.1, "meaning": "升高:溶血/肝细胞性/胆道梗阻,伴皮肤巩膜黄染需就医"},
    "低密度脂蛋白": {"unit": "mmol/L", "low": 0, "high": 3.4, "meaning": "心血管风险核心指标;极高危目标<1.8,高危<2.6"},
    "甘油三酯": {"unit": "mmol/L", "low": 0, "high": 1.7, "meaning": "≥5.6 有急性胰腺炎风险,须药物干预"},
    "促甲状腺激素": {"unit": "mIU/L", "low": 0.55, "high": 4.78, "meaning": "升高+FT4低=甲减;降低+FT4高=甲亢;亚临床期需复查"},
    "尿素氮": {"unit": "mmol/L", "low": 2.9, "high": 8.2, "meaning": "升高提示肾功能受损/脱水/消化道出血,结合肌酐判断"},
    "纤维蛋白原": {"unit": "g/L", "low": 2, "high": 4, "meaning": "降低:消耗性凝血病/DIC;升高:炎症/妊娠"},
    "肌酸激酶": {"unit": "U/L", "low": 30, "high": 200, "meaning": "显著升高:心肌梗死/横纹肌溶解/剧烈运动后,结合肌钙蛋白鉴别"},
}

# 药物相互作用(教学级常见组合)
DRUG_INTERACTIONS: dict[str, list[dict]] = {
    "华法林+阿司匹林": [
        {"severity": "major", "effect": "出血风险显著增加", "advice": "联用须严格评估血栓与出血获益比,加强 INR 监测"},
    ],
    "华法林+克拉霉素": [
        {"severity": "major", "effect": "克拉霉素抑制 CYP3A4,华法林代谢减慢,INR 升高", "advice": "避免联用或减量并加密 INP 监测"},
    ],
    "二甲双胍+碘造影剂": [
        {"severity": "moderate", "effect": "造影剂肾病风险下二甲双胍蓄积,乳酸酸中毒风险", "advice": "造影前后各停 48h,复查肾功能后恢复"},
    ],
    "地高辛+呋塞米": [
        {"severity": "moderate", "effect": "呋塞米致低钾,增加地高辛中毒风险", "advice": "监测血钾与地高辛浓度"},
    ],
    "他汀类+克拉霉素": [
        {"severity": "major", "effect": "横纹肌溶解风险升高", "advice": "暂停他汀或换用相互作用小的抗菌药"},
    ],
    "左甲状腺素+钙剂": [
        {"severity": "minor", "effect": "钙影响左甲状腺素吸收", "advice": "间隔至少 4 小时服用"},
    ],
    "甲巯咪唑+普萘洛尔": [
        {"severity": "none", "effect": "常规联合用于甲亢控制症状,无有害相互作用", "advice": "按医嘱使用"},
    ],
    "阿司匹林+布洛芬": [
        {"severity": "moderate", "effect": "布洛芬竞争结合位点,削弱阿司匹林的心血管保护作用", "advice": "错开至少 2 小时或换用对乙酰氨基酚"},
    ],
    "ACEI+螺内酯": [
        {"severity": "major", "effect": "高钾血症风险叠加", "advice": "监测血钾,限制钾摄入"},
    ],
    "辛伐他汀+葡萄柚汁": [
        {"severity": "moderate", "effect": "葡萄柚抑制 CYP3A4,他汀血药浓度升高,肌病风险", "advice": "治疗期间避免大量饮用葡萄柚汁"},
    ],
    "左氧氟沙星+钙剂": [
        {"severity": "moderate", "effect": "钙与喹诺酮螯合,吸收显著下降", "advice": "错开 2 小时以上服用"},
    ],
    "苯二氮卓类+酒精": [
        {"severity": "major", "effect": "中枢抑制叠加,呼吸抑制与意外风险显著升高", "advice": "严禁同用"},
    ],
    "二甲双胍+酒精": [
        {"severity": "moderate", "effect": "乳酸酸中毒风险增加", "advice": "避免大量饮酒"},
    ],
    "胺碘酮+华法林": [
        {"severity": "major", "effect": "胺碘酮抑制华法林代谢,INR 显著升高", "advice": "华法林减量约 1/3 并加密 INR 监测"},
    ],
}

# 症状/情况 → 科室映射(教学级)
DEPARTMENT_MAP: list[dict[str, Any]] = [
    {"keywords": ["胸痛", "心悸", "胸闷", "高血压"], "dept": "心血管内科", "urgent": ["胸痛伴大汗", "胸痛放射"]},
    {"keywords": ["咳嗽", "咳痰", "喘息", "气促", "哮喘"], "dept": "呼吸内科", "urgent": ["呼吸困难"]},
    {"keywords": ["腹痛", "腹泻", "恶心", "呕吐", "黑便", "呕血"], "dept": "消化内科", "urgent": ["呕血", "黑便", "剧烈腹痛"]},
    {"keywords": ["头痛", "头晕", "肢体无力", "言语不清", "抽搐"], "dept": "神经内科", "urgent": ["突发偏瘫", "言语不清", "剧烈头痛"]},
    {"keywords": ["多饮", "多尿", "体重下降", "甲状腺", "怕热多汗"], "dept": "内分泌科", "urgent": []},
    {"keywords": ["发热", "儿童", "患儿", "小孩"], "dept": "儿科(成人:感染科)", "urgent": ["3月龄以下发热", "精神萎靡"]},
    {"keywords": ["尿频", "尿急", "尿痛", "血尿"], "dept": "泌尿外科", "urgent": []},
    {"keywords": ["皮疹", "瘙痒", "荨麻疹"], "dept": "皮肤科", "urgent": ["皮疹压不褪色", "伴呼吸困难"]},
    {"keywords": ["腰背痛", "关节痛", "骨折", "扭伤"], "dept": "骨科", "urgent": ["外伤后畸形", "足下垂"]},
    {"keywords": ["情绪低落", "失眠", "焦虑", "抑郁"], "dept": "精神心理科", "urgent": ["自杀"]},
    {"keywords": ["月经", "阴道", "妊娠", "孕", "产检"], "dept": "妇产科", "urgent": ["阴道大出血", "胎动减少"]},
    {"keywords": ["贫血", "出血倾向", "瘀斑", "淋巴结肿大"], "dept": "血液科", "urgent": []},
    {"keywords": ["水肿", "蛋白尿", "肌酐", "尿蛋白"], "dept": "肾内科", "urgent": []},
    {"keywords": ["消瘦", "肿块", "肿瘤", "化疗"], "dept": "肿瘤科", "urgent": []},
    {"keywords": ["鼻塞", "耳鸣", "听力下降", "咽痛"], "dept": "耳鼻喉科", "urgent": ["呼吸困难"]},
    {"keywords": ["牙痛", "牙龈", "口腔溃疡"], "dept": "口腔科", "urgent": ["面部肿胀"]},
]

EMERGENCY_URIS = ["拨打 120", "急诊科立即就诊"]


def interpret_lab(item: str, value: float, patient: str = "成人") -> dict[str, Any]:
    """解读单项检验指标(对照参考区间)。"""
    ref = LAB_REFERENCE.get(item)
    if not ref:
        return {"error": f"暂不支持指标 {item!r}", "supported": list(LAB_REFERENCE)[:8] + ["..."]}
    try:
        v = float(value)
    except (TypeError, ValueError):
        return {"error": f"数值无效: {value!r}"}
    if v < ref["low"]:
        status, direction = "偏低", "↓"
    elif v > ref["high"]:
        status, direction = "偏高", "↑"
    else:
        status, direction = "正常", "→"
    return {
        "item": item, "value": v, "unit": ref["unit"],
        "reference_range": f"{ref['low']}-{ref['high']}",
        "status": status, "flag": direction,
        "patient": patient, "meaning": ref["meaning"],
        "note": "单次结果需结合临床与复测,勿自行诊断",
    }


def check_drug_interaction(drug_a: str, drug_b: str) -> dict[str, Any]:
    """查询两药相互作用。参数为通用名,如 '华法林' '克拉霉素'。"""
    a, b = drug_a.strip(), drug_b.strip()
    for key, records in DRUG_INTERACTIONS.items():
        parts = key.split("+")
        if (a in parts[0] or parts[0] in a) and (b in parts[1] or parts[1] in b) or \
           (a in parts[1] or parts[1] in a) and (b in parts[0] or parts[0] in b):
            return {"pair": key, "interactions": records,
                    "note": "相互作用信息仅供就医沟通参考,调整用药须由医师决定"}
    return {"pair": f"{a}+{b}", "interactions": [],
            "note": "教学库未收录该组合;无记录不等于无相互作用,请咨询医师或药师"}


def recommend_department(symptoms: str) -> dict[str, Any]:
    """根据症状描述推荐就诊科室。"""
    hits: list[dict] = []
    for m in DEPARTMENT_MAP:
        matched = [k for k in m["keywords"] if k in symptoms]
        if matched:
            hits.append({"dept": m["dept"], "matched": matched, "urgent": m["urgent"]})
    urgent_hit = any(u in symptoms for m in DEPARTMENT_MAP for u in m["urgent"])
    if not hits:
        return {"error": "未能识别症状,建议先挂全科/普通内科分诊",
                "hint": "尽量使用标准症状词,如 胸痛/腹痛/头痛"}
    hits.sort(key=lambda h: -len(h["matched"]))
    return {"recommend": hits[0]["dept"], "alternatives": [h["dept"] for h in hits[1:3]],
            "matched_keywords": hits[0]["matched"],
            "urgent": urgent_hit,
            "urgent_advice": ("识别到急症信号:立即拨打 120 或前往急诊!" if urgent_hit
                              else "如症状加重或出现急症表现,立即就医")}


# search_guideline 绑定 KnowledgeEngine(由 runner 注入,同 finagent 模式)
_kb_engine = None


def bind_guideline_engine(engine) -> None:
    global _kb_engine
    _kb_engine = engine


def search_guideline(query: str) -> dict[str, Any]:
    """检索临床指南知识卡(高血压分级/胸痛鉴别/溶栓时间窗等)。"""
    if _kb_engine is None:
        return {"error": "指南引擎未初始化"}
    hits = _kb_engine.search(query)
    return {"query": query,
            "results": [{"id": h["card"]["id"], "topic": h["card"]["topic"],
                         "content": h["card"]["content"], "score": h["score"]} for h in hits]}
