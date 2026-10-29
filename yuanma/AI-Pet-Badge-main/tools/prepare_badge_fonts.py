"""Build small, complete UI fonts from the fonts bundled with pinned LVGL."""
from pathlib import Path
import subprocess

root = Path(__file__).resolve().parents[1]
main = root / 'firmware/ai_pet_badge/main'
source = main.parent / 'managed_components/lvgl__lvgl/scripts/built_in_font'
converter = root / 'tools/fonts/node_modules/lv_font_conv/lv_font_conv.js'
symbols = '爱莉希雅深圳天气晴多云阴雾小中大暴雨雪雷阵冻毛毛冰雹待同步更新缓存未联网连接校时月日周一二三四五六年月陪伴动作休息轻触互动稍后已记下在你身边正在准备°℃·'
symbols += '头晕恢复'
fonts = [
    ('badge_ui_14', 'SourceHanSansSC-Normal.otf', 14, symbols, '0x20-0x7e'),
    ('badge_name_20', 'SourceHanSansSC-Normal.otf', 20, '爱莉希雅青岚', None),
    ('badge_clock_30', 'Montserrat-Medium.ttf', 30, '0123456789:-', None),
    ('badge_icons_26', 'FontAwesome5-Solid+Brands+Regular.woff', 26, None,
     '0xf185,0xf186,0xf0c2,0xf0e9,0xf2dc,0xf0e7'),
]
for name, filename, size, text, glyph_range in fonts:
    output = main / f'{name}.c'
    command = ['node', str(converter), '--font', str(source / filename), '--size', str(size),
               '--format', 'lvgl', '--bpp', '4', '--no-compress', '--no-kerning',
               '--lv-font-name', name, '--lv-include', 'lvgl.h', '-o', str(output)]
    if text:
        command += ['--symbols', text]
    if glyph_range:
        command += ['-r', glyph_range]
    subprocess.run(command, check=True)
    content = output.read_text(encoding='utf-8')
    content = content.replace(str(root), '<project>').replace(root.as_posix(), '<project>')
    output.write_text('\n'.join(line.rstrip() for line in content.splitlines()).rstrip() + '\n', encoding='utf-8')
licenses = root / 'firmware/ai_pet_badge/licenses/fonts'
licenses.mkdir(parents=True, exist_ok=True)
for family in ('SourceHanSansSC', 'FontAwesome5', 'Montserrat'):
    for path in (source / 'font_license' / family).rglob('*'):
        if path.is_file():
            text = path.read_text(encoding='utf-8')
            (licenses / f'{family}-{path.name}').write_text(
                '\n'.join(line.rstrip() for line in text.splitlines()).rstrip() + '\n', encoding='utf-8')
print('UI fonts and upstream licenses generated.')
