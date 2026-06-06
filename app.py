"""
BLUESTAR ENGINE v10.2.1 -- Streamlit Interface (Ameliore)
Compatible ENGINE.V9.py (legacy) et ENGINE.V9_v10.2.1.py (corrige)
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

# -- WeasyPrint (PDF natif cote serveur) --
try:
    from weasyprint import HTML as _WeasyHTML
    _HAS_WEASYPRINT = True
except Exception:  # noqa: BLE001 — WeasyPrint peut lever cffi.FFIError (non-OSError/ImportError)
    _HAS_WEASYPRINT = False


def _html_to_pdf_bytes(html_content: str) -> bytes:
    """Genere un PDF calibre en memoire via WeasyPrint. Retourne les bytes."""
    buf = io.BytesIO()
    _WeasyHTML(string=html_content).write_pdf(buf)
    return buf.getvalue()


# -- Import du moteur (cache_resource pour ne pas recharger a chaque interaction) --
@st.cache_resource(show_spinner=False)
def _load_engine():
    here = Path(__file__).parent
    candidates = [
        "ENGINE.V9_v10.2.1.py",  # version corrigee (prioritaire)
        "ENGINE.V9.py",          # legacy
        "ENGINE_V9.py",
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

# -- Config page --
st.set_page_config(
    page_title="BLUESTAR v10.2.1",
    page_icon="blue_circle",
    layout="wide",
    initial_sidebar_state="expanded",
)

# -- Sidebar --
with st.sidebar:
    st.markdown("### BLUESTAR SYSTEM")
    st.caption("FX Institutional Desk - v10 HYBRID V4")

    if _engine_mod is None:
        st.error("Moteur introuvable")
    else:
        st.success(f"Moteur charge : {_engine_name}")

    st.divider()

    if _HAS_WEASYPRINT:
        st.success("WeasyPrint actif -- PDF natif disponible")
    else:
        st.warning("WeasyPrint indisponible -- PDF en fallback HTML")
        st.caption("Ajoute weasyprint dans requirements.txt + libs systeme (cairo, pango)")

    st.divider()
    st.markdown("### Pipeline")
    st.markdown("""
    1. **Merge** -> bluestar_merged_*.json
    2. **Calendar** -> calendar.json (optionnel)
    3. **Engine** -> Scoring V4 + HTML/PDF
    """)

    st.divider()
    st.markdown("### Validation")
    st.markdown("""
    - Version schema >= 3.4.0
    - Assets count > 0
    - ATR cascade valide
    """)

    if st.button("Vider le cache", use_container_width=True):
        st.cache_resource.clear()
        st.cache_data.clear()
        st.success("Cache vide. Rechargez la page.")


# -- Header --
st.title("BLUESTAR ENGINE v10.2.1")
st.caption("FX Institutional Desk - Hybrid Absolute/Cross-Sectional V4 - Zero Regression")

if _engine_mod is None:
    st.error("Moteur introuvable. Verifie que ENGINE.V9_v10.2.1.py ou ENGINE.V9.py est dans le repo.")
    st.stop()

run_pipeline = _engine_mod.run_pipeline

# -- Upload des fichiers --
col1, col2 = st.columns(2)

with col1:
    merged_file = st.file_uploader(
        "Merged JSON (bluestar_merged_*.json)",
        type=["json"],
        key="merged",
        help="Output du merge pipeline (merge_app.py). Doit contenir meta.version >= 3.4.0",
    )

with col2:
    calendar_file = st.file_uploader(
        "Calendar JSON (calendar.json) -- optionnel",
        type=["json"],
        key="calendar",
        help="Calendrier economique parse. Si absent, le pipeline tourne en mode degrade (F7 MACRO = 1.0, pas de blackout).",
    )

# -- Preview des donnees uploades --
if merged_file:
    with st.expander("Apercu du merged JSON", expanded=False):
        try:
            merged_data = json.loads(merged_file.getvalue().decode("utf-8"))
            meta = merged_data.get("meta", {})
            assets = merged_data.get("assets", {})
            signals = merged_data.get("signals", [])

            c1, c2, c3, c4 = st.columns(4)
            c1.metric("Version", meta.get("version", "N/A"))
            c2.metric("Assets", len(assets))
            c3.metric("Signaux", len(signals))
            scanners = meta.get("scanners_detected", [])
            c4.metric("Scanners", ", ".join(scanners)[:30] if scanners else "N/A")

            # Validation version schema (FIX-E02)
            version = meta.get("version", "")
            if version:
                try:
                    v_parts = tuple(int(x) for x in version.split(".")[:3])
                    min_v = (3, 4, 0)
                    if v_parts < min_v:
                        st.warning(f"Version schema {version} < 3.4.0 -- risque de desynchronisation merge->engine")
                    else:
                        st.success(f"Version schema {version} compatible")
                except (ValueError, AttributeError):
                    st.warning(f"Version schema non parseable : {version}")
            else:
                st.error("Version schema absente -- merge_app.py obsolete ?")

            # Apercu des assets
            if assets:
                sample_sym = list(assets.keys())[:5]
                st.markdown(f"**Assets (top 5) :** {', '.join(sample_sym)}")

        except json.JSONDecodeError as e:
            st.error(f"JSON invalide : {e}")
        except Exception as e:  # noqa: BLE001 — display errors in UI, not fatal
            st.error(f"Erreur lecture : {e}")

# -- Bouton run --
run_disabled = merged_file is None
if st.button("Generer le rapport", type="primary", use_container_width=True, disabled=run_disabled):
    if not merged_file:
        st.error("Upload le fichier merged JSON d'abord.")
        st.stop()

    with st.spinner("Pipeline en cours..."):
        with tempfile.TemporaryDirectory() as tmpdir:
            merged_path = os.path.join(tmpdir, "merged.json")
            output_path = os.path.join(tmpdir, "report.html")

            with open(merged_path, "wb") as f:
                f.write(merged_file.getvalue())

            calendar_path = None
            if calendar_file:
                calendar_path = os.path.join(tmpdir, "calendar.json")
                with open(calendar_path, "wb") as f:
                    f.write(calendar_file.getvalue())

            try:
                # Appel du pipeline
                kwargs = {
                    "merged_path": merged_path,
                    "output_path": output_path,
                }
                if calendar_path:
                    kwargs["calendar_json_path"] = calendar_path

                html = run_pipeline(**kwargs)

                st.session_state["report_html"] = html
                st.session_state["report_pdf"] = None
                st.session_state["report_pdf_err"] = None

                # Generation PDF si WeasyPrint dispo
                if _HAS_WEASYPRINT:
                    with st.spinner("Generation PDF natif..."):
                        try:
                            st.session_state["report_pdf"] = _html_to_pdf_bytes(html)
                        except Exception as pdf_err:  # noqa: BLE001 — PDF errors are non-fatal, shown in UI
                            st.session_state["report_pdf_err"] = str(pdf_err)

                st.success("Rapport genere avec succes")

            except Exception as e:  # noqa: BLE001 — pipeline errors are surfaced via st.error
                st.error(f"Erreur pipeline : {e}")
                st.exception(e)
                st.stop()

# -- Affichage et telechargements --
if "report_html" in st.session_state:
    html = st.session_state["report_html"]

    # Onglets : Preview | Source
    tab_preview, tab_source = st.tabs(["Apercu", "Source HTML"])

    with tab_preview:
        st.components.v1.html(html, height=1800, scrolling=True)

    with tab_source:
        preview = html[:5000]
        if len(html) > 5000:
            preview += "\n... (truncated)"
        st.code(preview, language="html")

    # Telechargements
    st.divider()
    dl_col1, dl_col2 = st.columns(2)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M")

    with dl_col1:
        st.download_button(
            label="Telecharger HTML",
            data=html,
            file_name=f"bluestar_report_{timestamp}.html",
            mime="text/html",
            use_container_width=True,
        )

    with dl_col2:
        if _HAS_WEASYPRINT and st.session_state.get("report_pdf"):
            st.download_button(
                label="Telecharger PDF",
                data=st.session_state["report_pdf"],
                file_name=f"bluestar_report_{timestamp}.pdf",
                mime="application/pdf",
                use_container_width=True,
            )
        else:
            st.button("Telecharger PDF", disabled=True, use_container_width=True)
            if st.session_state.get("report_pdf_err"):
                err = st.session_state["report_pdf_err"]
                st.error(f"PDF err : {err[:100]}")

else:
    st.info("Upload le fichier merged JSON pour lancer le pipeline.")
    st.markdown("""
    **Fichiers requis :**
    - bluestar_merged_YYYYMMDD_HHMMutc.json -- output du merge engine (v3.4.3+)

    **Fichiers optionnels :**
    - calendar.json -- calendrier economique parse (Forex Factory)

    **Notes :**
    - Sans calendrier, le pipeline tourne en mode degrade : F7 MACRO = 1.0, pas de blackout.
    - WeasyPrint est requis pour le PDF natif. Sinon, utilisez le fallback HTML + impression navigateur.
    """)
