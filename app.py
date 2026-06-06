"""
BLUESTAR ENGINE — Streamlit Interface
Compatible ENGINE_V9.py (moteur actuel du repo)
"""
import importlib.util
import sys
import tempfile
import os
import io
import json
from pathlib import Path
from datetime import datetime

import streamlit as st

# ── WeasyPrint (PDF natif côté serveur) ──────────────────────────────────────
try:
    from weasyprint import HTML as _WeasyHTML
    _HAS_WEASYPRINT = True
except Exception:
    _HAS_WEASYPRINT = False


def _html_to_pdf_bytes(html: str) -> bytes:
    buf = io.BytesIO()
    _WeasyHTML(string=html).write_pdf(buf)
    return buf.getvalue()


# ── Import du moteur (chargé une seule fois) ─────────────────────────────────
@st.cache_resource(show_spinner=False)
def _load_engine():
    here = Path(__file__).parent
    candidates = [
        "ENGINE_V9.py",
        "ENGINE.V9_v10.2.1.py",
        "ENGINE.V9.py",
        "bluestar_engine_v9.py",
    ]
    for name in candidates:
        path = here / name
        if path.exists():
            spec = importlib.util.spec_from_file_location("bluestar_engine", path)
            mod = importlib.util.module_from_spec(spec)
            sys.modules["bluestar_engine"] = mod
            spec.loader.exec_module(mod)
            return mod, name
    return None, None


_engine_mod, _engine_name = _load_engine()

# ── Config page ───────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="BLUESTAR",
    page_icon="⭐",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ── Sidebar ───────────────────────────────────────────────────────────────────
with st.sidebar:
    st.markdown("### BLUESTAR SYSTEM")
    st.caption("FX Institutional Desk — v10 HYBRID V4")

    if _engine_mod is None:
        st.error("Moteur introuvable")
    else:
        st.success(f"Moteur chargé : {_engine_name}")

    st.divider()

    if _HAS_WEASYPRINT:
        st.success("WeasyPrint actif — PDF natif disponible")
    else:
        st.warning("WeasyPrint indisponible")
        st.caption("Vérifie requirements.txt (weasyprint>=61.0) et packages.txt")

    st.divider()
    st.markdown("### Pipeline")
    st.markdown("""
    1. **Merge** → bluestar_merged_*.json
    2. **Calendar** → calendar.json *(optionnel)*
    3. **Engine** → Scoring V4 + PDF
    """)

    st.divider()
    st.markdown("### Validation")
    st.markdown("""
    - Version schéma >= 3.4.0
    - Assets count > 0
    - ATR cascade valide
    """)

    if st.button("Vider le cache", use_container_width=True):
        st.cache_resource.clear()
        st.cache_data.clear()
        st.success("Cache vidé. Rechargez la page.")


# ── Header ────────────────────────────────────────────────────────────────────
st.title("BLUESTAR ENGINE")
if _engine_mod is not None:
    st.caption(f"FX Institutional Desk — Hybrid Absolute/Cross-Sectional V4 — moteur : {_engine_name}")
else:
    st.caption("FX Institutional Desk — Hybrid Absolute/Cross-Sectional V4")

if _engine_mod is None:
    st.error("Moteur introuvable. Vérifie que **ENGINE_V9.py** est présent dans le repo.")
    st.stop()

run_pipeline = _engine_mod.run_pipeline

# ── Upload des fichiers ───────────────────────────────────────────────────────
col1, col2 = st.columns(2)

with col1:
    merged_file = st.file_uploader(
        "Merged JSON (bluestar_merged_*.json)",
        type=["json"],
        key="merged",
        help="Output du merge pipeline. Doit contenir meta.version >= 3.4.0",
    )

with col2:
    calendar_file = st.file_uploader(
        "Calendar JSON (calendar.json) — optionnel",
        type=["json"],
        key="calendar",
        help="Calendrier économique parsé. Si absent : F7 MACRO = 1.0, pas de blackout.",
    )

# ── Aperçu du merged JSON ────────────────────────────────────────────────────
if merged_file:
    with st.expander("Aperçu du merged JSON", expanded=False):
        try:
            merged_data = json.loads(merged_file.getvalue().decode("utf-8"))
            meta    = merged_data.get("meta", {})
            assets  = merged_data.get("assets", {})
            signals = merged_data.get("signals", [])

            c1, c2, c3, c4 = st.columns(4)
            c1.metric("Version",  meta.get("version", "N/A"))
            c2.metric("Assets",   len(assets))
            c3.metric("Signaux",  len(signals))
            scanners = meta.get("scanners_detected", [])
            c4.metric("Scanners", ", ".join(scanners)[:30] if scanners else "N/A")

            version = meta.get("version", "")
            if version:
                try:
                    v_parts = tuple(int(x) for x in version.split(".")[:3])
                    if v_parts < (3, 4, 0):
                        st.warning(f"Version schéma {version} < 3.4.0 — risque de désynchronisation")
                    else:
                        st.success(f"Version schéma {version} compatible")
                except (ValueError, AttributeError):
                    st.warning(f"Version schéma non parseable : {version}")
            else:
                st.error("Version schéma absente — merge_app.py obsolète ?")

            if assets:
                st.markdown(f"**Assets (top 5) :** {', '.join(list(assets.keys())[:5])}")

        except json.JSONDecodeError as e:
            st.error(f"JSON invalide : {e}")
        except Exception as e:
            st.error(f"Erreur lecture : {e}")

# ── Bouton run ────────────────────────────────────────────────────────────────
if st.button("Générer le rapport", type="primary", use_container_width=True, disabled=merged_file is None):
    if not merged_file:
        st.error("Upload le fichier merged JSON d'abord.")
        st.stop()

    with st.spinner("Pipeline en cours…"):
        with tempfile.TemporaryDirectory() as tmpdir:
            merged_path = os.path.join(tmpdir, "merged.json")
            with open(merged_path, "wb") as f:
                f.write(merged_file.getvalue())

            calendar_path = None
            if calendar_file:
                calendar_path = os.path.join(tmpdir, "calendar.json")
                with open(calendar_path, "wb") as f:
                    f.write(calendar_file.getvalue())

            try:
                kwargs = {"merged_path": merged_path}
                if calendar_path:
                    kwargs["calendar_json_path"] = calendar_path

                html = run_pipeline(**kwargs)

                st.session_state["report_html"]    = html
                st.session_state["report_pdf"]     = None
                st.session_state["report_pdf_err"] = None

                if _HAS_WEASYPRINT:
                    with st.spinner("Génération PDF…"):
                        try:
                            st.session_state["report_pdf"] = _html_to_pdf_bytes(html)
                        except Exception as pdf_err:
                            st.session_state["report_pdf_err"] = str(pdf_err)

                st.success("Rapport généré ✓")

            except Exception as e:
                st.error(f"Erreur pipeline : {e}")
                st.exception(e)
                st.stop()

# ── Téléchargements ───────────────────────────────────────────────────────────
if "report_html" in st.session_state:
    html      = st.session_state["report_html"]
    timestamp = datetime.now().strftime("%Y%m%d_%H%M")

    st.divider()
    dl_col1, dl_col2 = st.columns(2)

    with dl_col1:
        if _HAS_WEASYPRINT and st.session_state.get("report_pdf"):
            st.download_button(
                label="⬇ Télécharger PDF",
                data=st.session_state["report_pdf"],
                file_name=f"bluestar_report_{timestamp}.pdf",
                mime="application/pdf",
                use_container_width=True,
            )
        else:
            st.button("⬇ Télécharger PDF", disabled=True, use_container_width=True)
            if st.session_state.get("report_pdf_err"):
                st.error(f"PDF err : {st.session_state['report_pdf_err'][:120]}")

    with dl_col2:
        st.download_button(
            label="⬇ Télécharger HTML",
            data=html,
            file_name=f"bluestar_report_{timestamp}.html",
            mime="text/html",
            use_container_width=True,
        )

else:
    st.info("Upload le fichier merged JSON pour lancer le pipeline.")
    st.markdown("""
    **Fichiers requis :**
    - `bluestar_merged_YYYYMMDD_HHMMutc.json` — output du merge engine (v3.4.3+)

    **Fichiers optionnels :**
    - `calendar.json` — calendrier économique parsé (Forex Factory)

    **Notes :**
    - Sans calendrier, F7 MACRO = 1.0 partout, aucun blackout appliqué.
    - WeasyPrint ≥ 61.0 requis pour le PDF natif.
    """)
