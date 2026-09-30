# -*- coding: utf-8 -*-
"""新增医学工具(第二批):症状自检流程 + 用药教育 + 儿童发热评估。

扩展工具面,覆盖更多真实医疗业务场景(JD#5 工具链协同)。
"""
from __future__ import annotations

from typing import Any

# 症状自查流程树:症状 → 分支问题 → 建议(教学级,来自常识性分诊逻辑)
SYMPTOM_FLOWS: dict[str, dict[str, Any]] = {
    "发热": {
        "questions": [
            {"q": "3月龄以下婴儿?", "yes": {"action": "emergency", "msg": "3月龄以下发热属急症,立即就医"}},
            {"q": "精神萎靡/拒食/抽搐/呼吸困难?", "yes": {"action": "emergency", "msg": "出现危险信号,立即就医"}},
            {"q": "发热超过3天或体温>40℃?", "yes": {"action": "clinic", "msg": "建议24小时内就医查明原因"}},
        ],
        "no_match": {"action": "self_care", "msg": "可先居家观察:补水+按需退热+监测;精神状态比体温更重要"},
    },
    "头痛": {
        "questions": [
            {"q": "突发剧烈头痛(一生最剧烈)?", "yes": {"action": "emergency", "msg": "警惕蛛网膜下腔出血,立即拨打120"}},
            {"q": "伴发热+颈僵硬?", "yes": {"action": "emergency", "msg": "警惕脑膜炎,立即就医"}},
            {"q": "伴肢体无力/言语不清/视物异常?", "yes": {"action": "emergency", "msg": "警惕卒中,立即拨打120"}},
            {"q": "进行性加重+晨起明显+呕吐?", "yes": {"action": "clinic_urgent", "msg": "需尽快排查颅内压增高/占位,1-2天内神经内科"}},
        ],
        "no_match": {"action": "self_care", "msg": "反复偏头痛/紧张型头痛可神经内科门诊评估;记录诱因"},
    },
    "腹痛": {
        "questions": [
            {"q": "剧烈腹痛持续不缓解或伴面色苍白/心率快?", "yes": {"action": "emergency", "msg": "立即急诊(警惕穿孔/出血/扭转)"}},
            {"q": "右下腹痛+发热?", "yes": {"action": "clinic_urgent", "msg": "警惕阑尾炎,当天普外科/急诊"}},
            {"q": "上腹痛+胸闷/放射左臂,中老年+心血管风险?", "yes": {"action": "emergency", "msg": "警惕心梗表现,立即就医查心电图"}},
            {"q": "育龄女性+停经+腹痛?", "yes": {"action": "emergency", "msg": "警惕宫外孕破裂,立即急诊"}},
        ],
        "no_match": {"action": "self_care", "msg": "轻度可观察;持续>6小时或加重即就医;就医前勿自行服强效止痛药(掩盖病情)"},
    },
    "咳嗽": {
        "questions": [
            {"q": "呼吸困难/口唇发绀/咯血?", "yes": {"action": "emergency", "msg": "立即就医"}},
            {"q": "超过3周(成人)或伴消瘦/盗汗/发热?", "yes": {"action": "clinic", "msg": "慢性咳嗽需排查结核/哮喘/胃食管反流等,呼吸内科"}},
            {"q": "婴幼儿+犬吠样咳嗽/喘鸣?", "yes": {"action": "clinic_urgent", "msg": "警惕喉炎,尽快就医"}},
        ],
        "no_match": {"action": "self_care", "msg": "感冒后咳嗽可自愈(可达3-8周);避免烟雾刺激;8周以上不缓解就诊"},
    },
    "胸痛": {
        "questions": [
            {"q": "压榨感/伴大汗/放射左臂或下颌/活动诱发?", "yes": {"action": "emergency", "msg": "警惕急性冠脉综合征,立即拨打120"}},
            {"q": "撕裂样痛向背部放射/双上肢血压差大?", "yes": {"action": "emergency", "msg": "警惕主动脉夹层,立即拨打120"}},
            {"q": "伴呼吸困难+近期制动/手术史?", "yes": {"action": "emergency", "msg": "警惕肺栓塞,立即就医"}},
        ],
        "no_match": {"action": "clinic", "msg": "非典型胸痛(与呼吸/体位相关,压痛点明确)多为肌肉骨骼性,可门诊评估;但新发胸痛建议先排除心源性"},
    },
    "皮疹": {
        "questions": [
            {"q": "伴面部口唇肿胀/喉头发紧/呼吸困难?", "yes": {"action": "emergency", "msg": "过敏性休克前兆,立即拨打120"}},
            {"q": "压不褪色+发热/精神差?", "yes": {"action": "emergency", "msg": "警惕脑膜炎球菌感染,立即就医"}},
            {"q": "单侧带状分布水疱+神经痛?", "yes": {"action": "clinic_urgent", "msg": "带状疱疹,72小时内抗病毒效果最佳"}},
        ],
        "no_match": {"action": "self_care", "msg": "一般荨麻疹可用二代抗组胺药;反复发作或原因不明者皮肤科就诊;记录可疑接触物"},
    },
}

# 用药教育库(常见药,教学级)
MEDICATION_EDUCATION: dict[str, dict[str, Any]] = {
    "二甲双胍": {"class": "双胍类降糖药", "key_points": ["随餐或餐后服用减少胃肠反应", "造影检查前后各停48小时", "长期使用关注维生素B12"],
                 "warning": "严重缺氧/肝肾功能不全者禁用;乳酸酸中毒罕见但严重"},
    "阿托伐他汀": {"class": "他汀类调脂药", "key_points": ["晚间服用", "避免大量葡萄柚汁", "出现不明肌痛/乏力/深色尿及时就诊(横纹肌溶解信号)"],
                   "warning": "与克拉霉素等CYP3A4抑制剂合用需谨慎"},
    "华法林": {"class": "抗凝药", "key_points": ["固定时间服药", "按医嘱定期测INR", "绿叶菜摄入保持稳定(勿忽多忽少)", "注意出血征象:牙龈出血/瘀斑/黑便/血尿"],
              "warning": "大量药物/食物相互作用,任何新增用药告知医生"},
    "氨氯地平": {"class": "钙拮抗剂降压药", "key_points": ["每日固定时间", "脚踝水肿常见,若明显告知医生", "起身缓慢防体位性低血压"],
                "warning": "葡萄柚汁可升高血药浓度"},
    "奥美拉唑": {"class": "质子泵抑制剂", "key_points": ["早餐前半小时服用", "不建议长期自行使用", "长期使用关注骨折/低镁/维生素B12风险"],
                "warning": "可能掩盖胃癌症状,报警症状(消瘦/吞咽困难/呕血)先查胃镜"},
    "左甲状腺素": {"class": "甲状腺激素", "key_points": ["晨起空腹服用,30分钟后进食", "与钙剂/铁剂间隔4小时", "剂量调整后6-8周复查甲功"],
                 "warning": "心悸/多汗/手抖提示过量"},
    "沙美特罗替卡松": {"class": "吸入制剂(哮喘/COPD)", "key_points": ["吸入后漱口防口腔念珠菌感染", "规律使用而非按需", "急救时用短效支气管扩张剂而非本药"],
                     "warning": "症状加重先就医评估,勿自行加量"},
    "恩格列净": {"class": "SGLT2i 降糖药", "key_points": ["兼具心肾保护获益", "泌尿生殖感染风险增加,注意个人卫生", "手术/重度疾病期间遵医嘱停用"],
               "warning": "罕见酮症酸中毒可发生在血糖不高时,恶心腹痛乏力及时就医"},
    "螺内酯": {"class": "保钾利尿剂", "key_points": ["定期查血钾与肾功能", "与ACEI/ARB联用时高钾风险叠加", "男性乳房发育为可逆性副作用"],
             "warning": "高钾血症表现:肌无力/心悸,需急查心电图"},
    "阿仑膦酸钠": {"class": "双膦酸盐(骨质疏松)", "key_points": ["晨起空腹用满杯白水送服", "服药后保持直立30分钟", "补充钙与维生素D"],
                "warning": "出现吞咽痛/胸骨后痛停药就医"},
    "孟鲁司特": {"class": "白三烯受体拮抗剂(哮喘/过敏性鼻炎)", "key_points": ["晚间服用", "颗粒剂可与少量软食混合"],
               "warning": "关注情绪变化/睡眠异常/行为异常(FDA黑框警示),出现即告知医生"},
    "硝酸甘油": {"class": "硝酸酯(心绞痛急救)", "key_points": ["舌下含服,坐位用药防跌倒", "含1片5分钟不缓解可再含1片,最多3片", "药片需避光密封保存,开瓶3-6月更换"],
               "warning": "连用3片不缓解拨打120(可能是心梗);与伟哥类药物严禁同用"},
    "胰岛素(基础)": {"class": "基础胰岛素", "key_points": ["固定时间注射", "注射部位轮换", "随身携带糖块防低血糖"],
                  "warning": "低血糖信号:心慌/冷汗/手抖/意识模糊→立即进食含糖食物;驾驶前测血糖"},
    "氯吡格雷": {"class": "抗血小板药", "key_points": ["勿自行停药(支架术后停药有血栓风险)", "注意出血征象", "择期手术前告知医生(通常停5-7天)"],
               "warning": "与奥美拉唑长期联用可能减弱效果,可咨询换泮托拉唑"},
}

# 用药依从性检查(慢病管理场景)
ADHERENCE_CHECKS: dict[str, list[str]] = {
    "高血压": ["是否每天固定时间服药?", "有无自行减量/停药?", "家庭自测血压记录了吗?", "限盐了吗(<5g/天)?"],
    "糖尿病": ["规律测血糖吗?", "知道低血糖怎么处理吗?", "足部每天检查了吗?", "年度眼底与尿蛋白查了吗?"],
    "哮喘": ["急救药随身带吗?", "能区分控制药与急救药吗?", "每月夜间憋醒超过2次吗?", "吸入技术定期复评吗?"],
    "抗凝": ["按时测INR了吗?", "知道出血征象吗?", "新增任何药物告知医生了吗?", "饮食绿叶菜摄入稳定吗?"],
}

# 儿童发热分级评估(教学级,基于 NICE 风险分层简化)
PEDI_FEVER_RISK: dict[str, list[str]] = {
    "high": [  # 任一命中 → 急症
        "3月龄以下", "精神萎靡", "抽搐", "呼吸困难", "皮疹压不褪色",
        "脱水(尿少/哭无泪/囟门凹陷)", "发热≥5天", "家长直觉严重不安",
    ],
    "intermediate": [  # 命中 → 尽快就医
        "3-6月龄", "体温≥39℃", "发热伴呕吐不能进食", "呼吸增快", "肢体活动减少",
    ],
}


def symptom_triage_flow(symptom: str, red_flags: str = "") -> dict[str, Any]:
    """按症状自查流程给出分诊建议。symptom 传标准症状词;red_flags 传用户描述中的伴随情况。"""
    flow = SYMPTOM_FLOWS.get(symptom)
    if not flow:
        return {"error": f"暂不支持症状 {symptom!r} 的自查流程",
                "supported": list(SYMPTOM_FLOWS)}
    desc = red_flags or ""
    triggered = []
    for q in flow["questions"]:
        # 问题文本与用户描述匹配(关键词粗匹配)
        keywords = [w for w in q["q"].replace("?", "").replace("(", "/").replace(")", "/").split("/") if len(w) >= 2]
        if any(k in desc for k in keywords):
            triggered.append({"question": q["q"], **q["yes"]})
            break  # 取最高级别命中
    if triggered:
        return {"symptom": symptom, "path": "red_flag", "result": triggered[0],
                "note": "本流程为教学级自查,不构成医疗建议"}
    return {"symptom": symptom, "path": "default",
            "result": flow["no_match"],
            "note": "本流程为教学级自查,不构成医疗建议;症状加重或持续请就医"}


def medication_education(drug: str) -> dict[str, Any]:
    """常用药品的用药教育信息(服用要点/警示)。"""
    edu = MEDICATION_EDUCATION.get(drug)
    if not edu:
        # 模糊匹配
        for name, v in MEDICATION_EDUCATION.items():
            if drug and (drug in name or name in drug):
                edu = v
                drug = name
                break
    if not edu:
        return {"error": f"暂无 {drug!r} 的用药教育资料", "supported": list(MEDICATION_EDUCATION)}
    return {"drug": drug, "class": edu["class"],
            "key_points": edu["key_points"], "warning": edu["warning"],
            "note": "具体用法用量以医嘱与说明书为准"}


def assess_child_fever(description: str) -> dict[str, Any]:
    """儿童发热危险分层(教学级 NICE 简化)。description 传家长描述。"""
    high_hit = [r for r in PEDI_FEVER_RISK["high"] if r.replace("(尿少/哭无泪/囟门凹陷)", "") in description
                or ("脱水" in r and "尿少" in description) or ("脱水" in r and "哭无泪" in description)]
    if high_hit:
        return {"risk": "high", "hits": high_hit,
                "advice": "存在高危信号,立即就医(急诊)",
                "note": "3月龄以下婴儿发热本身即属急症"}
    inter_hit = [r for r in PEDI_FEVER_RISK["intermediate"] if r in description]
    if inter_hit:
        return {"risk": "intermediate", "hits": inter_hit,
                "advice": "建议尽快(当天)就医评估",
                "note": "观察精神状态与进食,恶化升级为急症处理"}
    return {"risk": "low", "hits": [],
            "advice": "可居家观察:按需退热+补水;精神状态比体温数字更重要;发热≥5天或精神差立即就医",
            "note": "教学级评估,不构成医疗建议"}


# ----------------------------------------------------------------------
# 慢病随访计划生成(慢病管理场景,JD#5 业务闭环)
# ----------------------------------------------------------------------
CHRONIC_FOLLOWUP: dict[str, dict[str, Any]] = {
    "高血压": {
        "targets": "血压<140/90(合并糖尿病/肾病<130/80,能耐受时)",
        "monitor": [("家庭血压", "每天早晚各1次,连续7天后复诊评估", "血压计"),
                    ("肾功能+电解质", "起始用药后2-4周", "抽血"),
                    ("心电图", "每年1次", "检查")],
        "lifestyle": ["限钠<5g/天", "每周150分钟中等强度运动", "减重(BMI<24)", "限酒戒烟", "管理睡眠与压力"],
        "red_flags": ["血压≥180/110", "剧烈头痛/视物模糊", "胸痛/呼吸困难", "肢体无力/言语不清"],
        "review_cycle": "血压稳定后每3个月复诊;每月评估依从性",
    },
    "2型糖尿病": {
        "targets": "HbA1c<7%(老年/多种合并症可放宽至7.5-8%)",
        "monitor": [("HbA1c", "每3个月(稳定后可6个月)", "抽血"),
                    ("尿白蛋白/肌酐比", "每年1次", "尿检"),
                    ("眼底检查", "每年1次", "专科"),
                    ("足部检查", "每次复诊自查+每年专科1次", "体格")],
        "lifestyle": ["主食定量粗细搭配", "餐后运动30分钟", "足部每日检查护理", "随身携带糖块"],
        "red_flags": ["低血糖反复发作", "血糖>16.7伴恶心呕吐(酮症)", "足部伤口不愈/发黑", "视物突然模糊"],
        "review_cycle": "每3个月复诊;每年一次并发症系统筛查",
    },
    "哮喘": {
        "targets": "夜间症状<2次/月,无活动受限,肺功能接近个人最佳",
        "monitor": [("峰流速自我监测", "每天晨晚,记录日记", "峰流速仪"),
                    ("ACT控制测试", "每4周自评", "问卷"),
                    ("肺功能+FeNO", "每6-12个月或加重后", "检查"),
                    ("吸入技术复核", "每次复诊", "体格")],
        "lifestyle": ["识别并回避诱发因素(尘螨/花粉/宠物/冷空气)", "规律用控制药不自行停", "运动前热身或预防用药"],
        "red_flags": ["急救药使用>2次/周", "夜间憋醒频繁", "说话成句困难/口唇发绀", "峰流速降至个人最佳60%以下"],
        "review_cycle": "稳定期每1-3个月复诊;升级治疗2-4周内复评",
    },
    "慢性肾脏病": {
        "targets": "血压<130/80,尿蛋白尽量降低,eGFR下降速度<5ml/min/年",
        "monitor": [("肾功能+电解质", "每1-3个月(分期越晚越密)", "抽血"),
                    ("尿蛋白定量", "每3-6个月", "尿检"),
                    ("血红蛋白/铁代谢", "每3-6个月(贫血是CKD常见并发症)", "抽血"),
                    ("血磷/甲状旁腺激素", "G3b起定期", "抽血")],
        "lifestyle": ["限盐限蛋白(非透析0.6-0.8g/kg/天)", "避免肾毒性药物(NSAIDs/不明成分中草药)", "控制体重与血糖"],
        "red_flags": ["尿量明显减少", "水肿加重/气促", "血钾≥6.0(心悸肌无力)", "恶心呕吐食欲差(尿毒症倾向)"],
        "review_cycle": "按 CKD 分期:G1-G2 每年,G3a 每6月,G3b-G4 每1-3月",
    },
    "房颤(抗凝)": {
        "targets": "INR 2-3(华法林)或规律服用NOAC;心率静息<110(宽松目标)",
        "monitor": [("INR(华法林)", "稳定后每4周,调整期每3-7天", "抽血"),
                    ("肾功能(eGFR)", "NOAC每6-12月;CKD者更密", "抽血"),
                    ("心电图/动态心电图", "每年或症状变化时", "检查")],
        "lifestyle": ["绿叶菜摄入保持稳定", "戒酒或严格限酒", "避免头部外伤(抗凝出血风险)"],
        "red_flags": ["牙龈出血不止/大片瘀斑", "黑便/血尿/呕血", "剧烈头痛(颅内出血信号)", "新发肢体无力"],
        "review_cycle": "每3-6个月复诊评估血栓与出血风险",
    },
}


# 常见别名映射(缩写/口语 → 标准名)
ALIAS: dict[str, str] = {
    "CKD": "慢性肾脏病", "ckd": "慢性肾脏病",
    "肾功能不全": "慢性肾脏病", "肾衰": "慢性肾脏病",
    "T2DM": "2型糖尿病", "血糖高": "2型糖尿病",
    "心房颤动": "房颤(抗凝)", "房颤": "房颤(抗凝)",
    "心衰": "心力衰竭",
}


def _fuzzy_key(table: dict, key: str) -> tuple[str, dict] | None:
    """先别名,再精确,最后模糊(key 互含,或去掉常见后缀词后互含)。"""
    key = ALIAS.get(key, key)
    if key in table:
        return key, table[key]
    strip_words = ["病", "症", "2型", "Ⅱ型", "(抗凝)", "患者"]
    def norm(s: str) -> str:
        for w in strip_words:
            s = s.replace(w, "")
        return s
    for name, v in table.items():
        if key and (key in name or name in key):
            return name, v
    nk = norm(key)
    for name, v in table.items():
        nn = norm(name)
        if nk and nn and (nk in nn or nn in nk):
            return name, v
    return None


def generate_followup_plan(disease: str) -> dict[str, Any]:
    """生成慢病随访计划(监测项目/达标目标/生活方式/红旗征/复诊周期)。"""
    hit = _fuzzy_key(CHRONIC_FOLLOWUP, disease)
    if hit is None:
        return {"error": f"暂无 {disease!r} 的随访计划模板",
                "supported": list(CHRONIC_FOLLOWUP)}
    disease, plan = hit
    return {"disease": disease,
            "targets": plan["targets"],
            "monitor": [{"item": m[0], "frequency": m[1], "type": m[2]} for m in plan["monitor"]],
            "lifestyle": plan["lifestyle"],
            "red_flags": plan["red_flags"],
            "red_flag_action": "出现任一红旗征立即就医(部分属急症,拨打120)",
            "review_cycle": plan["review_cycle"],
            "note": "教学级模板,具体方案以主治医生制定为准"}


def check_adherence(disease: str) -> dict[str, Any]:
    """慢病用药依从性自查清单。"""
    hit = _fuzzy_key(ADHERENCE_CHECKS, disease)
    if hit is None:
        return {"error": f"暂无 {disease!r} 的依从性清单", "supported": list(ADHERENCE_CHECKS)}
    disease, items = hit
    return {"disease": disease, "checklist": items,
            "note": "任一回答'否'都建议复诊时与医生讨论;擅自停药是慢病失控首要原因"}


# ----------------------------------------------------------------------
# 食物/营养与孕哺安全(高频健康咨询)
# ----------------------------------------------------------------------
FOOD_GUIDANCE: dict[str, dict[str, Any]] = {
    "高钾食物": {"content": ["香蕉", "橙汁", "土豆", "菠菜", "蘑菇", "豆类", "低钠盐(氯化钾)"],
              "context": "CKD患者或服用保钾利尿剂/ACEI者需限制,高钾血症可致心律失常",
              "action": "慢性肾病患者勿用低钠盐,焯水可去部分钾"},
    "高嘌呤食物": {"content": ["动物内脏", "浓肉汤", "沙丁鱼/凤尾鱼", "贝类", "啤酒", "含糖饮料(果糖)"],
                "context": "高尿酸/痛风急性期严格避免,间歇期适量",
                "action": "急性期每天饮水>2000ml,限制酒精"},
    "维生素K丰富食物": {"content": ["菠菜", "西兰花", "动物肝脏", "绿茶"],
                   "context": "服用华法林者不是不能吃,而是保持摄入量稳定",
                   "action": "每日绿叶菜量尽量恒定,勿暴食勿突然不吃"},
    "孕期避免": {"content": ["酒精(无安全剂量)", "生肉/生鱼/未灭菌乳制品", "高汞鱼类(鲨鱼/剑鱼)", "未洗净蔬果(弓形虫)"],
              "context": "孕期饮食安全直接影响胎儿",
              "action": "肉类彻底煮熟;咨询产科后再用任何药物与补剂"},
    "哺乳期注意": {"content": ["酒精与吸烟", "大量咖啡因(<200mg/天为宜)", "部分药物可经乳汁"],
                "context": "哺乳期用药需告知医生哺乳状态",
                "action": "饮酒后至少2-3小时再哺乳;用药前查哺乳兼容性或咨询医生"},
    "空腹伤胃": {"content": ["阿司匹林/布洛芬等NSAIDs", "大量浓茶咖啡", "酒精"],
              "context": "NSAIDs空腹服增加胃黏膜损伤与出血风险",
              "action": "NSAIDs随餐或餐后服;胃病史者咨询医生加胃保护"},
}


def food_guidance(topic: str) -> dict[str, Any]:
    """常见食物/用药-食物相互作用指导。topic 支持口语(如'华法林不能吃什么')。"""
    # 口语改写:提取关键词后模糊匹配
    t = topic
    for kw, canon in [("华法林", "维生素K丰富食物"), ("抗凝", "维生素K丰富食物"),
                      ("钾", "高钾食物"), ("肾", "高钾食物"),
                      ("嘌呤", "高嘌呤食物"), ("痛风", "高嘌呤食物"),
                      ("怀孕", "孕期避免"), ("孕期", "孕期避免"), ("孕妇", "孕期避免"),
                      ("哺乳", "哺乳期注意"), ("空腹", "空腹伤胃")]:
        if kw in t:
            t = canon
            break
    hit = _fuzzy_key(FOOD_GUIDANCE, t) if t != topic else \
          (lambda: (topic, FOOD_GUIDANCE[topic]) if topic in FOOD_GUIDANCE else None)()
    if hit is None:
        # 剩余情况仍走一次模糊
        hit = _fuzzy_key(FOOD_GUIDANCE, t)
    if hit is None:
        return {"error": f"暂无 {topic!r} 的饮食指导", "supported": list(FOOD_GUIDANCE)}
    topic, g = hit
    return {"topic": topic, "foods": g["content"], "context": g["context"],
            "action": g["action"], "note": "教学级指导,具体遵循医嘱与营养师建议"}
