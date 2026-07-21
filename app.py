"""
BLUESTAR ENGINE v10.2.1 -- Streamlit Interface (Ameliore)
Compatible ENGINE.V9.py (legacy) et ENGINE.V9_v10.2.1.py (corrige)
"""
import hashlib
import importlib.util
import sys
import tempfile
import os
import json
from pathlib import Path
from datetime import datetime, timezone

import streamlit as st

# -- Traçabilité : hash du fichier moteur (calculé à chaque rerun, hors cache) --
def _engine_file_hash() -> str:
    """Retourne les 16 premiers chars du SHA-256 du fichier ENGINE.V9.py.

    Appelé à chaque rerun Streamlit (hors cache_resource). Coût < 5 ms.
    Retourne 'unavailable' si le fichier est introuvable — jamais bloquant.
    """
    try:
        path = Path(__file__).parent / "ENGINE.V9.py"
        return hashlib.sha256(path.read_bytes()).hexdigest()[:16]
    except OSError:
        return "unavailable"


# -- Import du moteur avec invalidation automatique par hash --
# AVANT : _load_engine()            — clé de cache constante → ancienne version
#         possible en mémoire après modification du fichier, sans avertissement.
# APRÈS : _load_engine(file_hash)   — le hash EST la clé de cache.
#         ENGINE.V9.py modifié → hash différent → rechargement automatique.
#         Fichier inchangé → même hash → entrée existante réutilisée (0 coût).
@st.cache_resource(show_spinner=False)
def _load_engine(file_hash: str):  # noqa: ARG001 — file_hash = clé de cache uniquement
    here = Path(__file__).parent
    path = here / "ENGINE.V9.py"
    if not path.exists():
        return None, None
    # Le nom dans spec_from_file_location doit rester stable.
    # Python 3.14 vérifie mod.__name__ == spec.name dans _check_name_wrapper :
    # modifier __name__ avant exec_module lève ImportError. On enregistre
    # sous la clé hashée APRÈS exec_module, sans toucher à __name__.
    spec = importlib.util.spec_from_file_location("bluestar_engine", path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["bluestar_engine"] = mod  # nom stable requis par exec_module
    spec.loader.exec_module(mod)
    # Alias hashé : permet la cohabitation de versions en mémoire.
    sys.modules[f"bluestar_engine_{file_hash}"] = mod
    return mod, "ENGINE.V9.py"


_engine_mod, _engine_name = _load_engine(_engine_file_hash())

# -- Config page --
st.set_page_config(
    page_title="BLUESTAR v10.2.1",
    page_icon="🔵",
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
        # PATCH-TRACE : bandeau d'identité moteur (traçabilité uniquement)
        _ver  = getattr(_engine_mod, "__version__",  "inconnu")
        _hash = getattr(_engine_mod, "__file_hash__", "inconnu")
        _lat  = getattr(_engine_mod, "__loaded_at__", None)
        _lat_str = _lat.strftime("%Y-%m-%d %H:%M UTC") if _lat else "inconnu"
        st.success(f"Moteur : {_engine_name}")
        st.caption(f"Version  `{_ver}`")
        st.caption(f"Hash     `{_hash[:8] if _hash != 'inconnu' else 'inconnu'}`")
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
        # PATCH-TRACE : le rerun suivant va recalculer le hash et recharger le moteur.
        st.success("Cache vide — moteur rechargé au prochain rerun.")
        st.rerun()


# -- Header --
st.title("BLUESTAR ENGINE v10.2.1")
st.caption("FX Institutional Desk - Hybrid Absolute/Cross-Sectional V4 - Zero Regression")

if _engine_mod is None:
    st.error("Moteur introuvable. Verifie que ENGINE.V9.py est dans le repo.")
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
                        st.warning(
                            f"Version schema {version} < 3.4.0"
                            " -- risque de desynchronisation merge->engine"
                        )
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
        except Exception as e:  # noqa: BLE001  # pylint: disable=broad-exception-caught
            st.error(f"Erreur lecture : {e}")

# -- Bouton run --
# -- PATCH-PROV (CAUSE RACINE #1) : cle de provenance des entrees courantes --
# Lie l'affichage du rapport aux inputs televerses. Aucune logique metier touchee.
def _inputs_fingerprint(mf, cf) -> str:
    h = hashlib.sha256()
    h.update(mf.getvalue() if mf else b"")
    h.update(b"|")
    h.update(cf.getvalue() if cf else b"")
    return h.hexdigest()

_cur_fp = _inputs_fingerprint(merged_file, calendar_file) if merged_file else None

# Invalidation : si les entrees ont change depuis la derniere generation reussie,
# on purge la sortie memorisee AVANT tout affichage (sinon rapport perime affiche).
if _cur_fp != st.session_state.get("report_fingerprint"):
    st.session_state.pop("report_html", None)
    st.session_state.pop("report_base_name", None)

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
                    # PATCH-IO (CAUSE RACINE #2) : confine l'ecriture PDF/fallback au
                    # tmpdir. Sans ceci, run_pipeline ecrit un artefact date dans le CWD.
                    "pdf_path": os.path.join(tmpdir, "report.pdf"),
                }
                if calendar_path:
                    kwargs["calendar_json_path"] = calendar_path

                html = run_pipeline(**kwargs)

                # Nom de fichier : date du rapport depuis le merged JSON déjà parsé
                try:
                    _ga = json.loads(merged_file.getvalue().decode("utf-8")).get("meta", {}).get("generated_at", "")
                    _rd = datetime.fromisoformat(_ga.replace("Z", "+00:00")).strftime("%Y.%m.%d")
                except Exception:  # noqa: BLE001
                    _rd = datetime.now().strftime("%Y.%m.%d")
                st.session_state["report_base_name"] = f"BLUESTAR FX Desk_Signal Report_{_rd}"
                st.session_state["report_html"] = html
                st.session_state["report_fingerprint"] = _cur_fp  # PATCH-PROV
                st.success("Rapport genere avec succes")

            except Exception as e:  # noqa: BLE001  # pylint: disable=broad-exception-caught
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

    _base_name = st.session_state.get("report_base_name", "BLUESTAR FX Desk_Signal Report")

    st.download_button(
        label="Telecharger HTML",
        data=html,
        file_name=f"{_base_name}.html",
        mime="text/html",
        use_container_width=True,
    )

else:
    st.info("Upload le fichier merged JSON pour lancer le pipeline.")
    st.markdown("""
    **Fichiers requis :**
    - bluestar_merged_YYYYMMDD_HHMMutc.json -- output du merge engine (v3.4.3+)

    **Fichiers optionnels :**
    - calendar.json -- calendrier economique parse (Forex Factory)

    **Notes :**
    - Sans calendrier, le pipeline tourne en mode degrade : F7 MACRO = 1.0, pas de blackout.
    - Le PDF se telecharge via le bouton integre dans le rapport HTML.
    """)
