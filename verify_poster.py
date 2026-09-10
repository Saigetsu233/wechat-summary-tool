# -*- coding: utf-8 -*-
"""不碰微信数据库，直接验证图片日报的三条出图路线。

用法（Windows PowerShell）：
    # 1) 零成本：只看整图海报的提示词，不调用任何接口
    python verify_poster.py --prompt-only

    # 2) 零成本：渲染本地排版兜底版（不需要 Key）
    python verify_poster.py --fallback

    # 3) 真出图：调用 gemini-3-pro-image 画整张手绘海报
    python verify_poster.py --key AIza...

样例数据是内置的。想用自己的真实内容，先用 --dump-digest 存一份 JSON，
改完再用 --digest 传回来。
"""

import argparse
import datetime
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from newspaper_renderer import render_newspaper, save_poster_image
from topic_image_generator import (
    GEMINI_POSTER_MODEL,
    POSTER_ASPECT_RATIO,
    POSTER_IMAGE_SIZE,
    build_full_poster_prompt,
    generate_full_poster,
)
from wechat_summary import _normalise_newspaper_digest, load_config


SAMPLE_RAW = {
    "headline": "摸出大未来",
    "lead": "今日社群聚焦职场现实生存战，群友从抗压心态聊至前沿 AI 辅助办公利弊，"
            "兼论方言传承及市井省钱秘笈。",
    "topics": [
        {
            "title": "职场抗压与工友生存战",
            "points": ["萝卜狗因内耗纠结辞职", "群友劝他降低预期", "低责任心应对压力"],
            "summary": "萝卜狗因内耗纠结辞职，红鲤躺平日记与 rin 以降低预期、保留精力"
                       "为利润等工友哲学予以开导。",
            "bubble": "先躺平再说",
            "visual_prompt": "exhausted office worker slumped on a desk while a shiba "
                             "dog coworker pats his shoulder",
        },
        {
            "title": "办公 AI 实测红黑榜",
            "points": ["碎月分享前沿代码模型", "吐槽 AI 做演示文稿错漏多", "唯文本总结尚能提效"],
            "summary": "群内激烈吐槽 AI 做演示文稿错漏百出，唯文本总结平稳提效。",
            "bubble": "又幻觉了",
            "visual_prompt": "person facepalming at a laptop showing a broken slide deck",
        },
        {
            "title": "粤语传承与南北日常",
            "points": ["下一代母语淡化危机", "南北认路方式差异", "记月份口诀分歧"],
            "summary": "群友探讨下一代在普通话教学与短视频影响下的母语淡化危机。",
            "bubble": "唔识讲粤语",
            "visual_prompt": "grandparent teaching a small child cantonese at a table",
        },
        {
            "title": "病患照护与民间偏方",
            "points": ["红鲤为家属求购支架", "群友及时劝阻偏方", "一致强调遵从医嘱"],
            "summary": "红鲤为卧床家属求购支架方案，打听偏方时被群友及时劝阻。",
            "bubble": "去正规医院",
            "visual_prompt": "family setting up a medical stand beside a bedridden relative",
        },
        {
            "title": "硬核增重与快餐代下单",
            "points": ["鸡蛋馒头速效增重法", "电商代下单优惠技巧", "客服响应经验互通"],
            "summary": "bill 分享加餐鸡蛋馒头速效增重法，群友同步交流代下单优惠技巧。",
            "bubble": "一天六个蛋",
            "visual_prompt": "person eating steamed buns and eggs beside a phone full of "
                             "food coupons",
        },
        {
            "title": "深夜放毒现场",
            "points": ["烤串炸鸡照片连发", "减脂计划集体下线"],
            "summary": "烤串、炸鸡和甜品照片接连出现，减脂计划在群体意志面前宣布暂停。",
            "bubble": "明天再减",
            "visual_prompt": "late night grilled skewers and fried chicken photos glowing "
                            "on a phone screen",
        },
    ],
    "mvp_rankings": [
        {
            "name": "红鲤躺平日记",
            "title": "硬核抗压首席工友",
            "reason": "紧挨领导高频输出，手搓百页文件并兼顾家庭照料，生存抗压拉满。",
            "visual_prompt": "calm chibi worker with sunglasses holding a tiny trophy",
        },
        {
            "name": "碎月",
            "title": "算力二王",
            "reason": "交替使用前沿付费开发模型，全天为群友做实测。",
            "visual_prompt": "cheerful chibi coder surrounded by floating code windows",
        },
        {
            "name": "rin",
            "title": "反内耗三王",
            "reason": "把工友哲学讲成金句，稳定输出安慰与吐槽。",
            "visual_prompt": "relaxed chibi person lying back with a mug and a crown",
        },
    ],
    "achievements": [
        {"award": "物理抗压大师", "name": "红鲤躺平日记", "reason": "工位紧贴领导依然稳定输出，心理素质坚如磐石。"},
        {"award": "算力大富翁", "name": "碎月", "reason": "交替使用前沿付费开发模型，举手投足尽显极客风采。"},
        {"award": "碳水轰炸机", "name": "bill", "reason": "三餐外加码朴素碳水与蛋类，数月增重二十斤。"},
        {"award": "反内耗心理导师", "name": "rin", "reason": "倡导拿多少报酬出多少力，视多余精力为自我利润。"},
        {"award": "麦门代购观察员", "name": "萝卜狗-NGA", "reason": "熟稔多平台代下单渠道与价差，省钱吃法研究透彻。"},
        {"award": "深夜放毒冠军", "name": "口冷", "reason": "一张烤串图让全群减脂计划集体下线。"},
    ],
    "quotes": [
        {"speaker": "红鲤躺平日记", "text": "工资低也是底气，能交差就行。"},
        {"speaker": "rin", "text": "多出来的精力才是自己的利润。"},
        {"speaker": "碎月", "text": "不是不能跑，是跑完人也下班了。"},
        {"speaker": "bill", "text": "减脂从明天开始，烤串今晚负责。"},
        {"speaker": "萝卜狗-NGA", "text": "省下来的钱才是吃到嘴里的。"},
        {"speaker": "口冷", "text": "偏方不能碰，该去医院就去。"},
        {"speaker": "红鲤躺平日记", "text": "手搓一百页，也就那么回事。"},
    ],
    "tomorrow_topics": [
        "AI 工具在实际办公中的准确率优化方案",
        "家庭护理辅助支架的后续使用反馈",
        "代下单渠道的价差追踪",
        "粤语教学资源的互相推荐",
        "增重食谱的第二周复盘",
    ],
    "special_notes": [
        "胃部疾患切勿轻信偏方，务必遵从三甲医院正规医嘱。",
        "代下单涉及个人资金往来，操作需注意防范交易风险。",
        "增重方法因人而异，请结合自身身体状况。",
        "群内玩梗仅代表当天语境，不作现实建议。",
        "AI 总结可能遗漏细节，原始记录优先。",
    ],
}


def _resolve_key(explicit):
    """Key 优先级：命令行 > 环境变量 > config.json 里保存的 Gemini Key。"""
    if str(explicit or "").strip():
        return explicit.strip()
    for name in ("GEMINI_API_KEY", "GOOGLE_API_KEY"):
        if str(os.environ.get(name) or "").strip():
            return os.environ[name].strip()
    keys = load_config().get("api_keys")
    if isinstance(keys, dict):
        return str(keys.get("gemini") or "").strip()
    return ""


def _load_digest(path):
    if path:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
        group_name = raw.pop("group_name", "我们的群聊")
        date_range = raw.pop("date", None) or str(datetime.date.today())
        count = raw.pop("message_count", 0)
    else:
        raw = json.loads(json.dumps(SAMPLE_RAW, ensure_ascii=False))
        group_name = "【NGA】-摸出大未来"
        date_range = str(datetime.date.today())
        count = 1782
    return _normalise_newspaper_digest(raw, group_name, date_range, int(count or 0))


def main():
    parser = argparse.ArgumentParser(description="验证图片日报出图效果")
    parser.add_argument("--key", default="", help="Gemini API Key；留空则读环境变量或 config.json")
    parser.add_argument("--model", default=GEMINI_POSTER_MODEL, help="图片模型名")
    parser.add_argument("--digest", default="", help="自定义 digest JSON 路径")
    parser.add_argument("--out", default="", help="输出 PNG 路径")
    parser.add_argument("--aspect", default=POSTER_ASPECT_RATIO, help="画幅比例，如 1:1 / 4:5")
    parser.add_argument("--size", default=POSTER_IMAGE_SIZE, help="出图分辨率档位，如 1K / 2K / 4K")
    parser.add_argument("--prompt-only", action="store_true", help="只打印提示词，不调用接口")
    parser.add_argument("--fallback", action="store_true", help="只渲染本地排版兜底版，不调用接口")
    parser.add_argument("--dump-digest", default="", help="把 digest 存成 JSON 供修改后复用")
    parser.add_argument("--open", action="store_true", help="出图后用系统看图工具打开")
    args = parser.parse_args()

    digest = _load_digest(args.digest)
    if args.dump_digest:
        Path(args.dump_digest).write_text(
            json.dumps(digest, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(f"digest 已导出：{args.dump_digest}")

    if args.prompt_only:
        prompt = build_full_poster_prompt(digest)
        print(prompt)
        print(f"\n---\n提示词长度：{len(prompt)} 字符")
        return 0

    stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    default_name = f"verify_{'fallback' if args.fallback else 'poster'}_{stamp}.png"
    out_path = args.out or str(Path.cwd() / default_name)

    if args.fallback:
        print("正在渲染本地排版兜底版（无插画）...")
        saved = render_newspaper(digest, out_path)
    else:
        api_key = _resolve_key(args.key)
        if not api_key:
            print(
                "没找到 Gemini API Key。请用 --key 传入，或先设置环境变量 GEMINI_API_KEY，"
                "或在主程序里保存一次 Gemini Key。\n"
                "想先零成本看效果：加 --prompt-only 或 --fallback。",
                file=sys.stderr,
            )
            return 2
        poster = generate_full_poster(
            digest,
            api_key,
            progress_callback=lambda message: print(f"  · {message}"),
            model=args.model,
            aspect_ratio=args.aspect,
            image_size=args.size,
        )
        saved = save_poster_image(poster, out_path)

    print(f"已保存：{saved}")
    if args.open and os.name == "nt":
        os.startfile(str(saved))  # noqa: S606
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
