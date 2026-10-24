"""The Volcano (豆包) 2.0 speakers this deployment offers, with human labels.

`voice` is the vendor's speaker id. The list is deliberately a curated subset, not the
whole catalogue: the console select should be usable, and every entry here is a 2.0 voice
that the `seed-tts-2.0` resource id accepts. The first three are the ones 9哥 asked for.
"""
from __future__ import annotations

from typing import Any

# speaker id -> label
VOLC_TTS_VOICES: dict[str, dict[str, str]] = {
    "zh_female_xiaohe_uranus_bigtts": {"zh": "小荷 2.0", "en": "Xiaohe 2.0"},
    "zh_female_vv_uranus_bigtts": {"zh": "Vivi 2.0", "en": "Vivi 2.0"},
    "zh_male_linjiananhai_uranus_bigtts": {"zh": "邻家男孩 2.0", "en": "Boy Next Door 2.0"},
    "zh_female_linjianvhai_uranus_bigtts": {"zh": "邻家女孩 2.0", "en": "Girl Next Door 2.0"},
    "zh_male_m191_uranus_bigtts": {"zh": "云舟 2.0", "en": "Yunzhou 2.0"},
    "zh_male_taocheng_uranus_bigtts": {"zh": "小天 2.0", "en": "Xiaotian 2.0"},
    "zh_male_liufei_uranus_bigtts": {"zh": "刘飞 2.0", "en": "Liufei 2.0"},
    "zh_female_qingxinnvsheng_uranus_bigtts": {"zh": "清新女声 2.0", "en": "Fresh Female 2.0"},
    "zh_female_tianmeixiaoyuan_uranus_bigtts": {"zh": "甜美小源 2.0", "en": "Sweet Xiaoyuan 2.0"},
    "zh_female_tianmeitaozi_uranus_bigtts": {"zh": "甜美桃子 2.0", "en": "Sweet Taozi 2.0"},
    "zh_female_shuangkuaisisi_uranus_bigtts": {"zh": "爽快思思 2.0", "en": "Bright Sisi 2.0"},
    "zh_female_kefunvsheng_uranus_bigtts": {"zh": "暖阳女声 2.0", "en": "Warm Female 2.0"},
    "zh_male_shaonianzixin_uranus_bigtts": {"zh": "少年梓辛 2.0", "en": "Young Zixin 2.0"},
    "zh_male_sunwukong_uranus_bigtts": {"zh": "猴哥 2.0", "en": "Monkey King 2.0"},
    "zh_female_xiaoxue_uranus_bigtts": {"zh": "儿童绘本 2.0", "en": "Storybook 2.0"},
    "zh_male_ruyayichen_uranus_bigtts": {"zh": "儒雅逸辰 2.0", "en": "Refined Yichen 2.0"},
    "zh_female_cancan_uranus_bigtts": {"zh": "知性灿灿 2.0", "en": "Thoughtful Cancan 2.0"},
    "zh_male_dayi_uranus_bigtts": {"zh": "大壹 2.0", "en": "Dayi 2.0"},
    "zh_female_peiqi_uranus_bigtts": {"zh": "佩奇猪 2.0", "en": "Peppa 2.0"},
    "zh_female_qingchezizi_uranus_bigtts": {"zh": "清澈梓梓 2.0", "en": "Clear Zizi 2.0"},
}


def voices_for(kind: str) -> list[dict[str, Any]]:
    """Console options for a provider kind: [{value, label{zh,en}}] (empty if none)."""
    if kind != "volc_tts":
        return []
    return [{"value": speaker, "label": dict(label)}
            for speaker, label in VOLC_TTS_VOICES.items()]
