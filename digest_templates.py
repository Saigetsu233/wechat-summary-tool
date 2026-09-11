"""Shared template catalogue for the picker, previews and image prompts."""

TEMPLATES = {
    'handdrawn': {
        'name': '手绘小报', 'tag': '热闹 · 漫画 · 群聊感',
        'paper': '#FFF9ED', 'ink': '#193F65', 'accent': '#F2AD41',
        'description': '彩色漫画分栏、人物小剧场，保留经典手绘风格。',
    },
    'editorial': {
        'name': '周末杂志', 'tag': '克制 · 高级 · 好阅读',
        'paper': '#F5F0E7', 'ink': '#272D2B', 'accent': '#DA6849',
        'description': '奶油纸色、衬线标题和编辑式插画，像一本轻松的独立杂志。',
        'brief': 'Independent editorial magazine. Warm ivory paper, charcoal ink, terracotta accents. '
                 'Elegant Chinese serif headlines, fine rules, asymmetrical two-column editorial layout. '
                 'One large topic illustration, smaller painterly spot illustrations for other stories. '
                 'No cartoon panel borders, no multicolored podiums. Use a refined member spotlight strip.',
    },
    'mint': {
        'name': '薄荷快报', 'tag': '清爽 · 轻盈 · 一眼重点',
        'paper': '#F1FAF6', 'ink': '#194E44', 'accent': '#63C9AB',
        'description': '薄荷绿与柔和留白，清晰的重点卡片，配少量俏皮插画。',
        'brief': 'Fresh modern mint newsletter. Off-white and pale mint paper, deep green text, '
                 'mint accents and tiny peach highlights. Airy rounded cards in a precise modular grid. '
                 'Clean Chinese sans-serif typography. Playful minimal editorial illustrations, '
                 'not emoji icons. Generous padding, light dividers, no heavy outlines or dark banner.',
    },
    'night': {
        'name': '午夜电台', 'tag': '深色 · 灵感 · 微霓虹',
        'paper': '#181D32', 'ink': '#ECEDF8', 'accent': '#B7A0FA',
        'description': '深蓝底色、淡紫亮点与夜间插画，适合晚间吃瓜特刊。',
        'brief': 'Midnight radio editorial digest. Deep navy paper, ivory highly legible Chinese text, '
                 'muted lavender and soft cyan accents. Refined cinematic ink illustrations. '
                 'Magazine grid with subtle luminous dividers, small star motifs, a strong masthead. '
                 'No blinding neon, no rainbow outlines. Quote section resembles a radio programme strip.',
    },
}


def get_template(key):
    return TEMPLATES.get(key, TEMPLATES['handdrawn'])


def template_layout_brief(key):
    template = get_template(key)
    return template.get('brief', '') + (
        '\nCreate one single square Chinese group-chat daily poster. Put group name, 群聊日报, '
        'date and message count in the masthead. Main stories take about half the page; '
        'member spotlights and achievements share the remaining main area. Quotes and notes form '
        'a compact bottom band. Adapt section sizes to actual content. Illustrations must depict '
        'each story, never replace or cover the text. Typeset the supplied Chinese faithfully; '
        'do not print design instructions. Do not invent names, facts, quotes or empty placeholder cards. '
        'Keep every line inside its section and readable. Omit empty sections.\n'
    )
