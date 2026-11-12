"""TalentIQ design system: brand tokens and the single global stylesheet.

All custom styling lives here so pages no longer duplicate inline ``<style>``
blocks. The injected CSS is a single block rendered once per page via
``apply_site_css()``.

The app is dark-first by design. Both the Streamlit theme (``.streamlit/
config.toml``) and this stylesheet force a near-black / deep-navy palette so no
light/white surface can ever render, regardless of OS theme or toolbar toggles.
"""

import streamlit as st

# ---------------------------------------------------------------------------
# Brand tokens (Python side, used by charts and inline markup)
# ---------------------------------------------------------------------------
DARK_BG = "#070B14"
SURFACE = "#0D1422"
SURFACE_2 = "#111827"
BORDER = "#1E293B"

PRIMARY = "#7C3AED"
PRIMARY_SOFT = "#A78BFA"
SECONDARY = "#06B6D4"
TEXT = "#F8FAFC"
MUTED = "#94A3B8"
SUCCESS = "#22C55E"
WARNING = "#F59E0B"
DANGER = "#EF4444"

CHART_COLORS = [
    PRIMARY,
    SECONDARY,
    SUCCESS,
    WARNING,
    DANGER,
    PRIMARY_SOFT,
    "#38BDF8",
]

BRAND_GRADIENT = (
    f"linear-gradient(100deg, {PRIMARY} 0%, {PRIMARY_SOFT} 45%, {SECONDARY} 100%)"
)


def site_theme() -> str:
    """Return the active theme type. Always interpreted as ``"dark"``."""
    try:
        theme_type = st.context.theme.type
        return theme_type or "dark"
    except Exception:
        return "dark"


def material_icon(name: str, size: int = 20) -> str:
    """A Material Symbols icon glyph (needs the font loaded by apply_site_css)."""
    return (
        f'<span class="material-symbols-outlined" '
        f'style="font-size:{size}px;vertical-align:-4px;">{name}</span>'
    )


# ---------------------------------------------------------------------------
# Global stylesheet
# ---------------------------------------------------------------------------
def apply_site_css() -> None:
    """Inject the shared TalentIQ stylesheet. Call once at the top of a page."""
    css = f"""
<style>
    /* CSS spec: @import must be the first rule in a <style> block or the
       browser silently drops it. Keep the Material Symbols font import here,
       before every other rule, so custom HTML icon glyphs actually render. */
    @import url('https://fonts.googleapis.com/css2?family=Material+Symbols+Outlined:opsz,wght,FILL,GRAD@20..48,300..600,0..1,-50..200&display=swap');

    .material-symbols-outlined {{
      font-family: 'Material Symbols Outlined' !important;
      font-weight: normal;
      font-style: normal;
      line-height: 1;
      letter-spacing: normal;
      text-transform: none;
      display: inline-block;
      white-space: nowrap;
      word-wrap: normal;
      direction: ltr;
      -webkit-font-smoothing: antialiased;
      font-variation-settings: 'FILL' 0, 'wght' 400, 'GRAD' 0, 'opsz' 24;
    }}

    /* ---------- Force the dark palette on Streamlit's own theme tokens ---------- */
    :root {{
      --background-color: {DARK_BG} !important;
      --secondary-background-color: {SURFACE} !important;
      --text-color: {TEXT} !important;
      --primary-color: {PRIMARY} !important;
      --link-color: {PRIMARY_SOFT} !important;
      --base-fg: {TEXT};
      --base-fg-muted: {MUTED};
      --base-bg: {DARK_BG};
      --base-bg2: {SURFACE};
      --base-border: {BORDER};

      /* Brand tokens */
      --tq-bg: {DARK_BG};
      --tq-surface: {SURFACE};
      --tq-surface-2: {SURFACE_2};
      --tq-primary: {PRIMARY};
      --tq-primary-soft: {PRIMARY_SOFT};
      --tq-secondary: {SECONDARY};
      --tq-success: {SUCCESS};
      --tq-warning: {WARNING};
      --tq-danger: {DANGER};
      --tq-muted: {MUTED};
      --tq-border: {BORDER};
      --tq-glow-opacity: 0.16;
      --tq-nav-blur: rgba(7, 11, 20, 0.82);
      --tq-chip-bg: rgba(124, 58, 237, 0.13);
      --tq-chip-text: #C4B5FD;
      --tq-card-hover-border: rgba(167, 139, 250, 0.5);
      --tq-radius: 14px;
      --tq-gradient: {BRAND_GRADIENT};
    }}

    /* ---------- App canvas: deep navy, subtle corner glows only ---------- */
    .stApp {{
      background-color: {DARK_BG};
      background-image:
        radial-gradient(900px 420px at 8% -8%, rgba(124, 58, 237, var(--tq-glow-opacity)), transparent 60%),
        radial-gradient(800px 380px at 100% -12%, rgba(6, 182, 212, calc(var(--tq-glow-opacity) * 0.65)), transparent 55%);
      background-attachment: fixed;
    }}
    .stApp, [data-testid="stAppViewContainer"] {{
      background-color: {DARK_BG} !important;
      color: {TEXT};
    }}

    [data-testid="stHeader"] {{
      background: transparent;
    }}

    [data-testid="stMain"] {{
      width: 100%;
      margin-left: 0;
    }}

    /*
     * Keep the main content column centered relative to the full viewport.
     * Streamlit lays the wide block container out full-bleed (no auto margins),
     * so at smaller widths the whole page — hero included — drifts to the left.
     * Width / max-width / margin auto re-center the wrapper without any
     * hardcoded offsets.
     */
    [data-testid="stMainBlockContainer"],
    .stMainBlockContainer,
    .block-container {{
      width: 100% !important;
      max-width: 1400px !important;
      margin-left: auto !important;
      margin-right: auto !important;
      padding-top: 1.1rem;
      padding-bottom: 3.5rem;
    }}

    /* Hide Streamlit's native heading anchor / "Link to heading" icons. */
    [data-testid="stHeaderActionElements"] {{ display: none !important; }}

    /* ---------- Native navigation (sidebar) is replaced by the top navbar ---------- */
    [data-testid="stSidebar"] {{ display: none; }}
    [data-testid="collapsedControl"] {{ display: none; }}

    /* ---------- Typography ---------- */
    h1, h2, h3, h4 {{ letter-spacing: -0.02em; color: {TEXT}; }}
    p, li {{ line-height: 1.6; color: {TEXT}; }}
    strong {{ color: {TEXT}; }}

    /* ---------- Top navigation bar: dark, elevated, premium ---------- */
    .tq-nav {{
      position: sticky;
      top: 0;
      z-index: 999;
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 1.25rem;
      flex-wrap: wrap;
      padding: 0.7rem 1.25rem;
      border: 1px solid rgba(30, 41, 59, 0.9);
      border-radius: var(--tq-radius);
      background: var(--tq-nav-blur);
      backdrop-filter: blur(16px);
      -webkit-backdrop-filter: blur(16px);
      box-shadow: 0 12px 32px rgba(0, 0, 0, 0.5), inset 0 1px 0 rgba(255, 255, 255, 0.04);
      margin-bottom: 1.5rem;
    }}

    .tq-brand {{
      font-size: 1.35rem;
      font-weight: 800;
      letter-spacing: -0.03em;
      text-decoration: none;
      color: {TEXT};
      white-space: nowrap;
    }}
    .tq-brand:hover {{ color: {TEXT}; }}
    .tq-brand > span {{
      background: var(--tq-gradient);
      -webkit-background-clip: text;
      background-clip: text;
      -webkit-text-fill-color: transparent;
      color: transparent;
    }}

    .tq-nav-links {{
      display: flex;
      align-items: center;
      gap: 0.5rem;
      flex-wrap: wrap;
    }}

    .tq-nav-link {{
      position: relative;
      padding: 0.45rem 0.85rem;
      border-radius: 10px;
      font-size: 0.92rem;
      font-weight: 500;
      color: {TEXT};
      text-decoration: none;
      opacity: 0.85;
      transition: background 0.18s ease, opacity 0.18s ease, color 0.18s ease;
    }}
    .tq-nav-link:hover {{
      background: rgba(124, 58, 237, 0.16);
      color: {TEXT};
      opacity: 1;
    }}
    .tq-nav-link.active {{
      background: linear-gradient(120deg, rgba(124, 58, 237, 0.3), rgba(6, 182, 212, 0.2));
      color: #fff;
      opacity: 1;
      box-shadow: inset 0 0 0 1px rgba(167, 139, 250, 0.4);
    }}
    .tq-nav-link.disabled {{
      cursor: not-allowed;
      opacity: 0.38;
    }}
    .tq-nav-link.disabled:hover {{
      background: transparent;
      opacity: 0.5;
    }}

    /* Auth-aware right zone (Sign In / user menu + Sign out) */
    .tq-nav-right {{
      display: flex;
      align-items: center;
      gap: 0.55rem;
      white-space: nowrap;
    }}
    .tq-nav-user {{
      display: inline-flex;
      align-items: center;
      gap: 0.45rem;
      padding: 0.4rem 0.8rem;
      border-radius: 10px;
      font-size: 0.85rem;
      font-weight: 600;
      color: var(--tq-primary-soft);
      background: rgba(124, 58, 237, 0.14);
      border: 1px solid rgba(167, 139, 250, 0.35);
    }}
    .tq-nav-user .material-symbols-outlined {{ color: var(--tq-secondary); }}
    .tq-nav-user-name {{ color: {TEXT}; }}
    .tq-nav-signin {{
      color: var(--tq-primary-soft);
      font-weight: 600;
    }}
    .tq-nav-signout {{ color: var(--tq-muted); }}
    .tq-nav-signout:hover {{ color: var(--tq-danger) !important; background: rgba(239, 68, 68, 0.12) !important; }}

    /* ---------- Hero ---------- */
    .tq-hero {{
      width: 100%;
      display: flex;
      flex-direction: column;
      align-items: center;
      text-align: center;
      padding: 3.2rem 1rem 2.6rem;
    }}
    .tq-eyebrow {{
      display: inline-flex;
      align-items: center;
      gap: 0.4rem;
      padding: 0.32rem 0.85rem;
      border-radius: 999px;
      font-size: 0.72rem;
      font-weight: 600;
      letter-spacing: 0.18em;
      text-transform: uppercase;
      color: var(--tq-primary-soft);
      border: 1px solid rgba(124, 58, 237, 0.4);
      background: rgba(124, 58, 237, 0.12);
    }}
    .tq-hero-title {{
      font-size: clamp(2rem, 5vw, 3.4rem);
      font-weight: 800;
      letter-spacing: -0.035em;
      line-height: 1.08;
      margin: 1.1rem auto 0.9rem;
      max-width: 880px;
      color: {TEXT};
    }}
    .tq-gradient-text {{
      background: var(--tq-gradient);
      -webkit-background-clip: text;
      background-clip: text;
      -webkit-text-fill-color: transparent;
      color: transparent;
    }}
    .tq-hero-subtitle {{
      font-size: 1.08rem;
      line-height: 1.7;
      color: var(--tq-muted);
      max-width: 620px;
      margin: 0 auto;
    }}
    .tq-hero-cta {{
      display: flex;
      gap: 0.85rem;
      justify-content: center;
      align-items: center;
      flex-wrap: wrap;
      margin: 1.8rem auto 0;
    }}
    .tq-hero-metrics {{
      display: flex;
      justify-content: center;
      gap: 1.4rem;
      flex-wrap: wrap;
      margin-top: 2.2rem;
      color: var(--tq-muted);
      font-size: 0.9rem;
    }}
    .tq-hero-metrics b {{ color: {TEXT}; font-weight: 600; }}

    /* ---------- Buttons: dark secondary surfaces, brand-gradient primary ---------- */
    .stButton > button,
    .stDownloadButton > button,
    .stFormSubmitButton > button {{
      border-radius: 10px !important;
      font-weight: 600 !important;
      background: {SURFACE_2} !important;
      border: 1px solid {BORDER} !important;
      color: {TEXT} !important;
      transition: transform 0.16s ease, box-shadow 0.16s ease, border-color 0.16s ease !important;
    }}
    .stButton > button:hover,
    .stDownloadButton > button:hover,
    .stFormSubmitButton > button:hover {{
      transform: translateY(-1px);
      border-color: rgba(167, 139, 250, 0.55) !important;
      box-shadow: 0 8px 22px rgba(0, 0, 0, 0.35);
    }}
    .stButton > button[kind="primary"],
    .stFormSubmitButton > button[kind="primary"] {{
      background: var(--tq-gradient) !important;
      border: none !important;
      color: #fff !important;
      box-shadow: 0 4px 18px rgba(124, 58, 237, 0.35);
    }}
    .stButton > button[kind="primary"]:hover,
    .stFormSubmitButton > button[kind="primary"]:hover {{
      box-shadow: 0 10px 26px rgba(124, 58, 237, 0.45);
    }}

    /* ---------- Cards: dark charcoal surfaces with a crisp top edge ---------- */
    [data-testid="stVerticalBlockBorderWrapper"] {{
      border-radius: var(--tq-radius);
      border-color: {BORDER} !important;
      background:
        linear-gradient(180deg, rgba(255, 255, 255, 0.025), rgba(255, 255, 255, 0) 42%),
        {SURFACE};
      box-shadow: inset 0 1px 0 rgba(255, 255, 255, 0.03), 0 6px 18px rgba(0, 0, 0, 0.22);
      transition: border-color 0.18s ease, box-shadow 0.18s ease;
    }}
    [data-testid="stVerticalBlockBorderWrapper"]:hover {{
      border-color: var(--tq-card-hover-border) !important;
      box-shadow: 0 8px 24px rgba(0, 0, 0, 0.3);
    }}

    /* ---------- Metrics ---------- */
    .tq-metric {{
      display: flex;
      flex-direction: column;
      gap: 0.3rem;
      padding: 0.15rem 0.1rem;
    }}
    .tq-metric-head {{
      display: flex;
      align-items: center;
      gap: 0.55rem;
      color: var(--tq-muted);
      font-size: 0.82rem;
      font-weight: 500;
      letter-spacing: 0.01em;
      text-transform: uppercase;
    }}
    .tq-metric-icon {{
      color: var(--tq-primary-soft);
      display: inline-flex;
    }}
    .tq-metric-value {{
      font-size: 2rem;
      font-weight: 700;
      letter-spacing: -0.03em;
      line-height: 1.1;
      color: {TEXT};
    }}
    .tq-metric-delta {{
      font-size: 0.82rem;
      color: var(--tq-muted);
    }}
    .tq-metric-value .unit {{
      font-size: 1.05rem;
      font-weight: 500;
      color: var(--tq-muted);
      margin-left: 0.15rem;
    }}

    /* ---------- Section header ---------- */
    .tq-section-title {{
      font-size: 1.32rem;
      font-weight: 700;
      letter-spacing: -0.02em;
      margin: 0;
      color: {TEXT};
    }}
    .tq-section-desc {{
      font-size: 0.9rem;
      color: var(--tq-muted);
      margin: 0.15rem 0 0;
    }}

    .tq-page-title {{
      font-size: clamp(1.6rem, 3vw, 2.15rem);
      font-weight: 800;
      letter-spacing: -0.03em;
      line-height: 1.1;
      margin: 0;
      color: {TEXT};
    }}
    .tq-page-subtitle {{
      font-size: 0.98rem;
      color: var(--tq-muted);
      margin: 0.35rem 0 0;
    }}

    /* ---------- Chips ---------- */
    .tq-chip-group {{
      display: flex;
      flex-wrap: wrap;
      gap: 0.45rem;
    }}
    .tq-chip {{
      display: inline-flex;
      align-items: center;
      gap: 0.4rem;
      padding: 0.28rem 0.7rem;
      border-radius: 999px;
      font-size: 0.82rem;
      font-weight: 500;
      color: var(--tq-chip-text);
      background: var(--tq-chip-bg);
      border: 1px solid rgba(124, 58, 237, 0.28);
      white-space: nowrap;
    }}
    .tq-chip.tq-chip-success {{
      color: var(--tq-success);
      background: rgba(34, 197, 94, 0.1);
      border-color: rgba(34, 197, 94, 0.32);
    }}
    .tq-chip.tq-chip-warning {{
      color: var(--tq-warning);
      background: rgba(245, 158, 11, 0.1);
      border-color: rgba(245, 158, 11, 0.32);
    }}
    .tq-chip.tq-chip-danger {{
      color: var(--tq-danger);
      background: rgba(239, 68, 68, 0.1);
      border-color: rgba(239, 68, 68, 0.32);
    }}
    .tq-chip.tq-chip-muted {{
      color: var(--tq-muted);
      background: rgba(148, 163, 184, 0.08);
      border-color: var(--tq-border);
    }}

    /* ---------- Badges ---------- */
    .tq-badge {{
      display: inline-flex;
      align-items: center;
      gap: 0.35rem;
      padding: 0.22rem 0.65rem;
      border-radius: 999px;
      font-size: 0.75rem;
      font-weight: 600;
      letter-spacing: 0.02em;
    }}
    .tq-badge-success {{ color: var(--tq-success); background: rgba(34, 197, 94, 0.12); border: 1px solid rgba(34, 197, 94, 0.35); }}
    .tq-badge-warning {{ color: var(--tq-warning); background: rgba(245, 158, 11, 0.12); border: 1px solid rgba(245, 158, 11, 0.35); }}
    .tq-badge-danger  {{ color: var(--tq-danger); background: rgba(239, 68, 68, 0.12); border: 1px solid rgba(239, 68, 68, 0.35); }}
    .tq-badge-info    {{ color: var(--tq-secondary); background: rgba(6, 182, 212, 0.12); border: 1px solid rgba(6, 182, 212, 0.35); }}
    .tq-badge-neutral {{ color: var(--tq-muted); background: rgba(148, 163, 184, 0.1); border: 1px solid var(--tq-border); }}

    /* ---------- Progress bars ---------- */
    .tq-progress {{
      width: 100%;
      height: 8px;
      border-radius: 999px;
      background: {SURFACE_2};
      overflow: hidden;
    }}
    .tq-progress > div {{
      height: 100%;
      border-radius: 999px;
      background: var(--tq-gradient);
      transition: width 0.9s cubic-bezier(0.22, 1, 0.36, 1);
    }}
    .tq-progress.tq-progress-success > div {{ background: linear-gradient(90deg, #16A34A, #4ADE80); }}
    .tq-progress.tq-progress-warning > div {{ background: linear-gradient(90deg, #D97706, #FACC15); }}
    .tq-progress.tq-progress-danger  > div {{ background: linear-gradient(90deg, #DC2626, #F87171); }}
    .tq-progress.tq-progress-thin {{ height: 4px; }}

    /* ---------- Skill bars ---------- */
    .tq-skill-bars {{ margin-top: 0.5rem; }}
    .tq-skill-bar-row {{
      display: flex;
      align-items: center;
      gap: 0.75rem;
      margin: 0.7rem 0;
    }}
    .tq-skill-label {{
      flex: 0 0 7.5rem;
      text-align: right;
      white-space: nowrap;
      overflow: hidden;
      text-overflow: ellipsis;
      color: {TEXT};
      font-size: 0.88rem;
      font-weight: 500;
    }}
    .tq-skill-track {{
      flex: 1;
      height: 8px;
      border-radius: 999px;
      background: rgba(148, 163, 184, 0.12);
      overflow: hidden;
    }}
    .tq-skill-track > div {{
      height: 100%;
      border-radius: 999px;
      background: var(--tq-gradient);
      transition: width 0.9s cubic-bezier(0.22, 1, 0.36, 1);
    }}
    .tq-skill-bar-row.tq-skill-gap .tq-skill-track > div {{
      background: linear-gradient(90deg, #7C3AED, #F59E0B);
    }}
    .tq-skill-count {{
      flex: 0 0 2.2rem;
      text-align: right;
      color: var(--tq-muted);
      font-size: 0.85rem;
    }}

    /* ---------- Insight cards ---------- */
    .tq-insight {{
      border-left: 3px solid var(--tq-primary);
      padding: 0.1rem 0.1rem 0.1rem 1rem;
      margin: 0.55rem 0;
    }}
    .tq-insight-title {{
      display: flex;
      align-items: center;
      gap: 0.5rem;
      font-weight: 600;
      margin: 0 0 0.3rem;
      color: {TEXT};
    }}
    .tq-insight-body {{ color: {TEXT}; font-size: 0.95rem; }}

    /* ---------- Divider with label ---------- */
    .tq-divider {{
      display: flex;
      align-items: center;
      gap: 0.8rem;
      margin: 1.6rem 0 1.1rem;
      color: var(--tq-muted);
      font-size: 0.78rem;
      font-weight: 600;
      letter-spacing: 0.12em;
      text-transform: uppercase;
    }}
    .tq-divider::before, .tq-divider::after {{
      content: "";
      flex: 1;
      height: 1px;
      background: linear-gradient(90deg, transparent, var(--tq-border), transparent);
    }}

    /* ---------- Loading state ---------- */
    .tq-loading {{
      display: flex;
      align-items: center;
      justify-content: center;
      gap: 0.7rem;
      padding: 0.6rem 0 0.4rem;
      color: var(--tq-muted);
      font-size: 0.92rem;
    }}
    .tq-spinner {{
      width: 22px;
      height: 22px;
      border-radius: 50%;
      border: 2.5px solid rgba(124, 58, 237, 0.25);
      border-top-color: var(--tq-primary);
      animation: tq-spin 0.8s linear infinite;
    }}
    @keyframes tq-spin {{ to {{ transform: rotate(360deg); }} }}

    /* ---------- Empty / error / success states ---------- */
    .tq-state {{
      text-align: center;
      padding: 2.6rem 1.2rem;
    }}
    .tq-state-icon {{
      font-size: 2.6rem;
      line-height: 1;
      margin-bottom: 0.7rem;
      color: var(--tq-muted);
    }}
    .tq-state-icon.tq-state-icon-error {{ color: var(--tq-danger); }}
    .tq-state-icon.tq-state-icon-success {{ color: var(--tq-success); }}
    .tq-state-title {{
      font-size: 1.15rem;
      font-weight: 700;
      letter-spacing: -0.01em;
      margin: 0 0 0.35rem;
      color: {TEXT};
    }}
    .tq-state-message {{
      font-size: 0.93rem;
      color: var(--tq-muted);
      max-width: 440px;
      margin: 0 auto;
      text-align: center;
    }}

    /* ---------- Candidate cards ---------- */
    .tq-candidate-name {{
      font-size: 1.25rem;
      font-weight: 700;
      letter-spacing: -0.02em;
      margin: 0;
      color: {TEXT};
    }}
    .tq-rank {{
      font-size: 0.8rem;
      font-weight: 700;
      color: var(--tq-primary-soft);
      letter-spacing: 0.08em;
      text-transform: uppercase;
    }}

    /* ---------- File uploader ---------- */
    [data-testid="stFileUploaderDropzone"] {{
      border-radius: var(--tq-radius);
      border-color: {BORDER} !important;
      background: {SURFACE} !important;
      color: {TEXT};
      transition: border-color 0.18s ease, background 0.18s ease;
    }}
    [data-testid="stFileUploaderDropzone"]:hover {{
      border-color: rgba(167, 139, 250, 0.5) !important;
      background: {SURFACE_2} !important;
    }}
    [data-testid="stFileUploaderDropzone"] [data-testid="stFileUploaderDropzoneInput"] {{
      color: {TEXT};
    }}
    [data-testid="stFileUploaderFile"] {{ color: {TEXT}; }}
    [data-testid="stFileUploaderFile"] button {{ color: {MUTED}; }}

    /* ---------- Inputs ---------- */
    .stTextArea textarea,
    .stTextInput div[data-baseweb="input"] > div {{
      background-color: {SURFACE} !important;
      color: {TEXT} !important;
    }}
    .stTextArea textarea::placeholder,
    .stTextInput input::placeholder {{ color: {MUTED} !important; }}
    [data-baseweb="input"] {{ background-color: {SURFACE} !important; }}
    [data-testid="stNumberInput"] input {{ background-color: {SURFACE} !important; color: {TEXT} !important; }}

    /* ---------- Select / multiselect / popover surfaces ---------- */
    [data-baseweb="select"] > div,
    [data-baseweb="select"] > div * {{ background-color: {SURFACE} !important; color: {TEXT} !important; }}
    [data-baseweb="popover"] [data-baseweb="menu"],
    [data-baseweb="popover"] [role="listbox"] {{
      background-color: {SURFACE_2} !important;
      color: {TEXT} !important;
    }}
    [data-baseweb="popover"] li, [data-baseweb="popover"] li div {{ color: {TEXT} !important; }}
    [data-baseweb="tag"] {{ background-color: rgba(124, 58, 237, 0.25) !important; color: {TEXT} !important; }}
    [data-baseweb="tag"] span {{ color: {TEXT} !important; }}

    /* ---------- Tabs ---------- */
    .stTabs [data-baseweb="tab-list"] {{
      gap: 0.35rem;
      border-bottom: 1px solid {BORDER};
    }}
    .stTabs [data-baseweb="tab"] {{
      border-radius: 10px 10px 0 0;
      padding: 0.55rem 1rem;
      color: {MUTED};
      transition: background 0.16s ease, color 0.16s ease;
    }}
    .stTabs [data-baseweb="tab"]:hover {{
      background: rgba(124, 58, 237, 0.12);
      color: {TEXT};
    }}
    .stTabs [aria-selected="true"] {{
      background: linear-gradient(180deg, rgba(124, 58, 237, 0.22), rgba(124, 58, 237, 0.06)) !important;
      color: #fff !important;
      box-shadow: inset 0 2px 0 var(--tq-gradient);
    }}

    /* ---------- Sliders ---------- */
    .stSlider [data-baseweb="slider"] div[role="slider"] {{ color: var(--tq-primary-soft); }}

    /* ---------- Dataframes ---------- */
    [data-testid="stDataFrame"] {{
      border-radius: var(--tq-radius);
      overflow: hidden;
      background-color: {SURFACE};
      border: 1px solid {BORDER};
    }}
    [data-testid="stDataFrame"] canvas {{ background-color: {SURFACE} !important; }}

    /* ---------- Alerts ---------- */
    .stAlert {{
      border-radius: var(--tq-radius);
      border: 1px solid {BORDER};
    }}

    /* ---------- Dialogs / toasts ---------- */
    [data-testid="stDialog"] {{ background-color: {SURFACE_2} !important; border: 1px solid {BORDER}; }}

    /* ---------- Footer ---------- */
    .tq-footer {{
      margin-top: 3.5rem;
      padding-top: 1.4rem;
      border-top: 1px solid var(--tq-border);
      display: flex;
      justify-content: space-between;
      align-items: center;
      gap: 1rem;
      flex-wrap: wrap;
      color: var(--tq-muted);
      font-size: 0.85rem;
    }}
    .tq-footer .tq-brand {{ font-size: 1.05rem; }}
    .tq-footer span {{ color: var(--tq-muted); }}

    /* ---------- Responsive ---------- */
    @media (max-width: 900px) {{
      .block-container {{ padding-top: 0.8rem; }}
      .tq-nav {{ padding: 0.55rem 0.9rem; }}
      .tq-nav-links {{ gap: 0.25rem; }}
      .tq-nav-link {{ padding: 0.35rem 0.6rem; font-size: 0.85rem; }}
    }}
    @media (max-width: 640px) {{
      .tq-hero {{ padding-top: 2rem; }}
      .tq-metric-value {{ font-size: 1.6rem; }}
      .tq-footer {{ justify-content: center; text-align: center; }}
    }}
</style>
"""

    st.markdown(css, unsafe_allow_html=True)