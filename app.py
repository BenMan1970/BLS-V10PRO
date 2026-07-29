"""
BLUESTAR ENGINE v10.2.1 -- Streamlit Interface (Ameliore)
Compatible ENGINE.V10.py (mise a jour roadmap) et ENGINE.V9.py (legacy)
"""
import hashlib
import importlib.util
import sys
import tempfile
import os
import json
from pathlib import Path
from datetime import datetime
from typing import Optional

import streamlit as st

# -- Config page DOIT être la première commande Streamlit --
st.set_page_config(
    page_title="BLUESTAR v10.2.1",
    page_icon="🔵",
    layout="wide",
    initial_sidebar_state="expanded",
)

# -- Détection dynamique du fichier moteur --
def _find_engine_file() -> Optional[Path]:
    here = Path(__file__).parent
    for name in ("ENGINE.V10.py", "ENGINE.V9.py", "ENGINE.py"):
        p = here / name
        if p.exists():
            return p
    return None

_engine_path = _find_engine_file()

def _engine_file_hash() -> str:
    if not _engine_path:
        return "unavailable"
    try:
        return hashlib.sha256(_engine_path.read_bytes()).hexdigest()[:16]
    except OSError:
        return "unavailable"

# -- Import du moteur avec gestion d'erreur explicite --
@st.cache_resource(show_spinner=False)
def _load_engine(file_hash: str, engine_path: Path):
    if not engine_path:
        return None, None, "Fichier moteur introuvable. Attendu: ENGINE.V10.py ou ENGINE.V9.py à la racine du repo."
    try:
        spec = importlib.util.spec_from_file_location("bluestar_engine", engine_path)
        mod = importlib.util.module_from_spec(spec)
        sys.modules["bluestar_engine"] = mod
        spec.loader.exec_module(mod)
        sys.modules[f"bluestar_engine_{file_hash}"] = mod
        # __version__ vient du module s'il le définit ; __file_hash__ et
        # __loaded_at__ sont propriétés du CHARGEMENT, pas du fichier source,
        # donc calculées ici plutôt qu'attendues dans le module.
        mod.__file_hash__ = file_hash
        mod.__loaded_at__ = datetime.utcnow()
        return mod, engine_path.name, None
    except Exception as e:
        # On capture l'erreur exacte (ex: ModuleNotFoundError) sans planter l'app
        return None, None, f"Erreur de chargement du moteur:\n\n{e}"

_engine_mod, _engine_name, _engine_err = _load_engine(_engine_file_hash(), _engine_path)

# -- Sidebar --
with st.sidebar:
    st.markdown("### BLUESTAR SYSTEM")
    st.caption("FX Institutional Desk - v10 HYBRID V4")

    if _engine_mod is None:
        st.error("Moteur introuvable ou erreur de chargement")
    else:
        _ver  = getattr(_engine_mod, "__version__",  "inconnu")
        _hash = getattr(_engine_mod, "__file_hash__", "unavailable")
        _lat  = getattr(_engine_mod, "__loaded_at__", None)
        _lat_str = _lat.strftime("%Y-%m-%d %H:%M UTC") if _lat else "inconnu"
        
        st.success(f"Moteur : {_engine_name}")
        st.caption(f"Version  `{_ver}`")
        st.caption(f"Hash     `{_hash[:8] if _hash != 'unavailable' else 'inconnu'}`")
        st.caption(f"Chargé   `{_lat_str}`")

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
        st.success("Cache vidé — moteur rechargé au prochain rerun.")
        st.rerun()

# -- Header --
_header_ver = getattr(_engine_mod, "__version__", None)
st.title(f"BLUESTAR ENGINE v{_header_ver}" if _header_ver else "BLUESTAR ENGINE")
st.caption("FX Institutional Desk - Hybrid Absolute/Cross-Sectional V4 - Zero Regression")

if _engine_mod is None:
    st.error("Le moteur n'a pas pu être chargé.")
    if _engine_err:
        st.code(_engine_err, language='python')
        st.info("Si l'erreur est `ModuleNotFoundError`, ajoute le module manquant dans `requirements.txt`.")
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
        help=(
            "Calendrier economique parse. Si absent, le pipeline tourne"
            " en mode degrade (F7 MACRO = 1.0, pas de blackout)."
        ),
    )

# Lecture unique en mémoire
merged_bytes = merged_file.getvalue() if merged_file else None
calendar_bytes = calendar_file.getvalue() if calendar_file else None

# -- Preview des donnees uploades --
if merged_bytes:
    with st.expander("Apercu du merged JSON", expanded=False):
        try:
            merged_data = json.loads(merged_bytes.decode("utf-8"))
            meta = merged_data.get("meta", {})
            assets = merged_data.get("assets", {})
            signals = merged_data.get("signals", [])

            c1, c2, c3, c4 = st.columns(4)
            c1.metric("Version", meta.get("version", "N/A"))
            c2.metric("Assets", len(assets))
            c3.metric("Signaux", len(signals))
            scanners = meta.get("scanners_detected", [])
            c4.metric("Scanners", ", ".join(scanners)[:30] if scanners else "N/A")

            version = meta.get("version", "")
            if version:
                try:
                    v_parts = tuple(int(x) for x in (version.split(".") + ["0", "0"])[:3])
                    min_v = (3, 4, 0)
                    if v_parts < min_v:
                        st.warning(f"Version schema {version} < 3.4.0 -- risque de desynchronisation")
                    else:
                        st.success(f"Version schema {version} compatible")
                except (ValueError, AttributeError):
                    st.warning(f"Version schema non parseable : {version}")
            else:
                st.error("Version schema absente -- merge_app.py obsolete ?")

            if assets:
                sample_sym = list(assets.keys())[:5]
                st.markdown(f"**Assets (top 5) :** {', '.join(sample_sym)}")

        except json.JSONDecodeError as e:
            st.error(f"JSON invalide : {e}")
        except Exception as e:
            st.error(f"Erreur lecture : {e}")

# -- Bouton run --
def _inputs_fingerprint(m_bytes: bytes, c_bytes: bytes) -> str:
    h = hashlib.sha256()
    h.update(m_bytes if m_bytes else b"")
    h.update(b"|")
    h.update(c_bytes if c_bytes else b"")
    return h.hexdigest()

_cur_fp = _inputs_fingerprint(merged_bytes, calendar_bytes) if merged_file else None

if _cur_fp != st.session_state.get("report_fingerprint"):
    st.session_state.pop("report_html", None)
    st.session_state.pop("report_base_name", None)
    st.session_state.pop("report_pdf_bytes", None)

run_disabled = merged_file is None
if st.button("Generer le rapport", type="primary", use_container_width=True, disabled=run_disabled):
    if not merged_file:
        st.error("Upload le fichier merged JSON d'abord.")
        st.stop()

    with st.spinner("Pipeline en cours..."):
        with tempfile.TemporaryDirectory() as tmpdir:
            merged_path = os.path.join(tmpdir, "merged.json")
            output_path = os.path.join(tmpdir, "report.html")
            pdf_path = os.path.join(tmpdir, "report.pdf")

            with open(merged_path, "wb") as f:
                f.write(merged_bytes)

            calendar_path = None
            if calendar_file:
                calendar_path = os.path.join(tmpdir, "calendar.json")
                with open(calendar_path, "wb") as f:
                    f.write(calendar_bytes)

            try:
                kwargs = {
                    "merged_path": merged_path,
                    "output_path": output_path,
                    "pdf_path": pdf_path,
                }
                if calendar_path:
                    kwargs["calendar_json_path"] = calendar_path

                html = run_pipeline(**kwargs)

                try:
                    _ga = json.loads(merged_bytes.decode("utf-8")).get("meta", {}).get("generated_at", "")
                    _rd = datetime.fromisoformat(_ga.replace("Z", "+00:00")).strftime("%Y.%m.%d")
                except Exception:
                    _rd = datetime.now().strftime("%Y.%m.%d")
                
                st.session_state["report_base_name"] = f"BLUESTAR FX Desk_Signal Report_{_rd}"
                st.session_state["report_html"] = html
                st.session_state["report_fingerprint"] = _cur_fp
                
                pdf_bytes = None
                if os.path.exists(pdf_path):
                    with open(pdf_path, "rb") as f_pdf:
                        pdf_bytes = f_pdf.read()
                st.session_state["report_pdf_bytes"] = pdf_bytes
                
                st.success("Rapport généré avec succès")

            except Exception as e:
                st.error(f"Erreur pipeline : {e}")
                st.exception(e)
                st.stop()

# -- Affichage et telechargements --
if "report_html" in st.session_state:
    html = st.session_state["report_html"]

    tab_preview, tab_source = st.tabs(["Apercu", "Source HTML"])

    with tab_preview:
        st.components.v1.html(html, height=1800, scrolling=True)

    with tab_source:
        preview = html[:5000]
        if len(html) > 5000:
            preview += "\n... (truncated)"
        st.code(preview, language="html")

    st.divider()

    _base_name = st.session_state.get("report_base_name", "BLUESTAR FX Desk_Signal Report")
    _pdf_data = st.session_state.get("report_pdf_bytes")

    col_dl1, col_dl2 = st.columns(2)
    
    with col_dl1:
        st.download_button(
            label="Télécharger HTML",
            data=html,
            file_name=f"{_base_name}.html",
            mime="text/html",
            use_container_width=True,
        )
    
    with col_dl2:
        if _pdf_data:
            st.download_button(
                label="Télécharger PDF",
                data=_pdf_data,
                file_name=f"{_base_name}.pdf",
                mime="application/pdf",
                use_container_width=True,
            )
        else:
            st.info("Le PDF n'a pas été généré par le moteur.")

else:
    st.info("Upload le fichier merged JSON pour lancer le pipeline.")
