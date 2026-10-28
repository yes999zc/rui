"""`/v2/devices` is one card per device, not one section per room.

The view has no JS test runner, so this mirrors the source-level guard already
used by `test_real_layout_5devices.py` and `test_audit_fixes.py`; `npm run
typecheck` and `npm run build` stay the executable frontend gates.
"""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VIEW = ROOT / "web/src/v2/views/DevicesView.vue"
STYLE = ROOT / "web/src/v2/style.css"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_three_devices_render_three_cards_not_three_room_sections() -> None:
    """N devices -> N cards: the grid's only loop is over the flat `cards` list."""
    text = _read(VIEW)
    grid_start = text.index('class="v2-device-grid"')
    grid = text[grid_start:text.index("</template>")]

    # One card element inside one loop over the flattened per-device list.
    assert grid.count('class="v2-device-card"') == 1
    assert grid.count('v-for="card in cards"') == 1
    assert grid.count("<RouterLink") == 1
    # The old room grouping is gone: no <section> per room in the list.
    assert 'v-for="group in groups"' not in text
    assert '<section class="v2-room"' not in text
    assert "<section" not in grid

    # `cards` emits exactly one entry per device (room only supplies the order).
    assert "order.flatMap((room) =>" in text
    assert "(byRoom.get(room) ?? []).map((device) => ({" in text
    assert "roomCount = computed(() => new Set(cards.value.map((card) => card.room)).size)" in text


def test_card_header_is_room_title_with_device_id_and_model_subtitle() -> None:
    text = _read(VIEW)
    room_title = text[text.index('class="v2-device-title"'):text.index('class="v2-device-badges"')]

    assert '<h2 class="v2-device-room">' in room_title
    assert "t('v2.devices.unassigned')" in room_title
    assert 'class="v2-device-card-sub mono"' in room_title
    assert "card.device.id" in room_title
    assert "card.device.model || t('v2.devices.modelFallback')" in room_title
    # Status and the default-output badge share the top-right column.
    badges = text[text.index('class="v2-device-badges"'):text.index('class="v2-device-sub"')]
    assert "card.defaultOutput" in badges
    assert 't(\'v2.devices.defaultOutput\')' in badges
    assert "<StatusBadge" in badges


def test_grid_breakpoints_are_three_then_two_then_one_column() -> None:
    css = _read(STYLE)
    assert "grid-template-columns: repeat(auto-fit, minmax(320px, 1fr));" in css

    tablet = css[css.index("@media (min-width: 768px) and (max-width: 1199px)"):]
    tablet = tablet[:tablet.index("}") + 1]
    assert ".v2-device-grid" in tablet
    assert "grid-template-columns: repeat(2, minmax(0, 1fr));" in tablet

    phone = css[css.index("@media (max-width: 767px)"):]
    phone = phone[:phone.index("}") + 1]
    assert ".v2-device-grid" in phone
    assert "grid-template-columns: minmax(0, 1fr);" in phone

    # Hover is border/shadow only: no movement and no transform transition.
    card_start = css.index(".v2-device-card {")
    card = css[card_start:css.index(".v2-device-top", card_start)]
    assert "transform" not in card
    hover = css[css.index(".v2-device-card:hover"):]
    hover = hover[:hover.index("}") + 1]
    assert "border-color: var(--line-strong);" in hover
    assert "box-shadow: var(--shadow-1);" in hover
    assert "transform" not in hover


def test_devices_actions_keep_their_testids_and_stay_above_the_grid() -> None:
    text = _read(VIEW)
    for testid in ("devices-add", "devices-first-run", "devices-empty-add"):
        assert f'data-testid="{testid}"' in text, testid

    grid_at = text.index('class="v2-device-grid"')
    assert text.index('class="v2-summary"') < grid_at
    assert 'class="v2-card v2-warnings"' in text
    assert text.index('class="v2-card v2-warnings"') < grid_at
