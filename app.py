"""
BLUESTAR ENGINE — Interface Streamlit v10.2.8
Compatible ENGINE.V10.py (roadmap) et ENGINE.V9.py / ENGINE.py (legacy).

Déploiement Streamlit Community Cloud :
  - AUCUNE dépendance apt (pas de packages.txt) -> l'installeur apt de l'image
    Cloud est cassé (dépôt bullseye-security expiré).
  - WeasyPrint est donc absent : le moteur produit le HTML calibré A4 et le
    PDF s'obtient par impression navigateur (Ctrl/Cmd+P -> Enregistrer en PDF).
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import platform
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

import streamlit as st
from streamlit.components.v1 import html as st_html

# ── Config page : DOIT rester la première commande Streamlit ────────────────
st.set_page_config(
    page_title="BLUESTAR v10.2.8",
    page_icon="🔵",
    layout="wide",
    initial_sidebar_state="expanded",
)

ENGINE_CANDIDATES = ("ENGINE.V10.py", "ENGINE.V9.py", "ENGINE.py")
MIN_SCHEMA = (3, 4, 0)


# ════════════════════════════════════════════════════════════════════════════
# Détection + chargement du moteur
# ════════════════════════════════════════════════════════════════════════════
def _find_engine_file() -> Optional[Path]:
    here = Path(__file__).resolve().parent
    for name in ENGINE_CANDIDATES:
        p = here / name
        if p.is_file():
            return p
    return None


def _file_hash(p: Optional[Path]) -> str:
    if p is None:
        return "unavailable"
    try:
        return hashlib.sha256(p.read_bytes()).hexdigest()[:16]
    except OSError:
        return "unavailable"


@st.cache_resource(show_spinner=False)
def _load_engine(engine_path_str: Optional[str], file_hash: str):
    """Charge le moteur en module isolé. Ne lève jamais : retourne l'erreur."""
    if not engine_path_str:
        return None, None, (
            "Fichier moteur introuvable à la racine du repo.\n"
            f"Attendu : {' ou '.join(ENGINE_CANDIDATES)}"
        )
    engine_path = Path(engine_path_str)
    try:
        spec = importlib.util.spec_from_file_location("bluestar_engine", engine_path)
        if spec is None or spec.loader is None:
            return None, None, f"Spec d'import illisible pour {engine_path.name}"
        mod = importlib.util.module_from_spec(spec)
        sys.modules["bluestar_engine"] = mod
        spec.loader.exec_module(mod)
        # __file_hash__/__loaded_at__ sont des propriétés du CHARGEMENT, pas du
        # fichier source : calculées ici, pas attendues dans le module.
        mod.__file_hash__ = file_hash
        mod.__loaded_at__ = datetime.now(timezone.utc)
        return mod, engine_path.name, None
    except Exception as exc:  # noqa: BLE001
        return None, None, f"{type(exc).__name__}: {exc}"


_engine_path = _find_engine_file()
_engine_mod, _engine_name, _engine_err = _load_engine(
    str(_engine_path) if _engine_path else None, _file_hash(_engine_path)
)

_HAS_PDF = bool(getattr(_engine_mod, "_HAS_WEASYPRINT", False)) if _engine_mod else False
_PDF_ERR = str(getattr(_engine_mod, "_WEASYPRINT_ERROR", "")) if _engine_mod else ""


# ════════════════════════════════════════════════════════════════════════════
# Sidebar
# ════════════════════════════════════════════════════════════════════════════
with st.sidebar:
    st.markdown("### BLUESTAR SYSTEM")
    st.caption("FX Institutional Desk — v10 HYBRID V4")

    if _engine_mod is None:
        st.error("Moteur non chargé")
    else:
        _ver = getattr(_engine_mod, "__version__", "inconnu")
        _hash = getattr(_engine_mod, "__file_hash__", "unavailable")
        _lat = getattr(_engine_mod, "__loaded_at__", None)
        st.success(f"Moteur : {_engine_name}")
        st.caption(f"Version  `{_ver}`")
        st.caption(f"Hash     `{_hash[:8] if _hash != 'unavailable' else 'inconnu'}`")
        st.caption(
            "Chargé   `"
            + (_lat.strftime("%Y-%m-%d %H:%M UTC") if _lat else "inconnu")
            + "`"
        )

    st.divider()
    st.markdown("### Rendu PDF")
    if _HAS_PDF:
        st.success("WeasyPrint actif — PDF natif calibré A4")
    else:
        st.info(
            "WeasyPrint indisponible sur cet hébergement.\n\n"
            "Le rapport HTML embarque déjà la feuille `@page A4` : "
            "ouvre-le puis **Imprimer → Enregistrer en PDF** "
            "(marges nulles, échelle 100 %)."
        )

    st.divider()
    st.markdown("### Pipeline")
    st.markdown(
        "1. **Merge** → `bluestar_merged_*.json`\n"
        "2. **Calendar** → `calendar.json` (optionnel)\n"
        "3. **Engine** → Scoring V4 + HTML"
    )

    st.divider()
    st.markdown("### Validation")
    st.markdown(
        "- Version schema ≥ 3.4.0\n"
        "- Assets count > 0\n"
        "- ATR cascade valide"
    )

    with st.expander("Diagnostic environnement", expanded=False):
        st.caption(f"Python `{platform.python_version()}`")
        st.caption(f"Streamlit `{st.__version__}`")
        st.caption(f"WeasyPrint `{'OK' if _HAS_PDF else 'absent'}`")
        if _PDF_ERR and not _HAS_PDF:
            st.code(_PDF_ERR, language="text")

    if st.button("Vider le cache", use_container_width=True):
        st.cache_resource.clear()
        st.cache_data.clear()
        st.rerun()


# ════════════════════════════════════════════════════════════════════════════
# Header
# ════════════════════════════════════════════════════════════════════════════
_header_ver = getattr(_engine_mod, "__version__", None) if _engine_mod else None
st.title(f"BLUESTAR ENGINE v{_header_ver}" if _header_ver else "BLUESTAR ENGINE")
st.caption("FX Institutional Desk — Hybrid Absolute/Cross-Sectional V4 — Zero Regression")

if _engine_mod is None:
    st.error("Le moteur n'a pas pu être chargé.")
    if _engine_err:
        st.code(_engine_err, language="text")
    st.info(
        "Si l'erreur est un `ModuleNotFoundError`, ajoute le module manquant "
        "dans `requirements.txt`. Si c'est un `OSError: cannot load library "
        "'pango-1.0-0'`, c'est WeasyPrint : il doit rester un import optionnel "
        "dans le moteur (bloc `try/except` autour de `from weasyprint import HTML`)."
    )
    st.stop()

run_pipeline = getattr(_engine_mod, "run_pipeline", None)
if run_pipeline is None:
    st.error(f"`run_pipeline` absent de {_engine_name} — moteur incompatible.")
    st.stop()


# ════════════════════════════════════════════════════════════════════════════
# Upload
# ════════════════════════════════════════════════════════════════════════════
col1, col2 = st.columns(2)

with col1:
    merged_file = st.file_uploader(
        "Merged JSON (bluestar_merged_*.json)",
        type=["json"],
        key="merged",
        help="Output du merge pipeline. Doit contenir meta.version ≥ 3.4.0",
    )

with col2:
    calendar_file = st.file_uploader(
        "Calendar JSON (calendar.json) — optionnel",
        type=["json"],
        key="calendar",
        help=(
            "Calendrier économique parsé. Si absent, le pipeline tourne en mode "
            "dégradé (F7 MACRO en fail-closed, pas de blackout)."
        ),
    )

merged_bytes: Optional[bytes] = merged_file.getvalue() if merged_file else None
calendar_bytes: Optional[bytes] = calendar_file.getvalue() if calendar_file else None


# ── Aperçu du merged JSON ───────────────────────────────────────────────────
def _parse_version(v: str) -> Optional[tuple[int, int, int]]:
    try:
        parts = (str(v).split(".") + ["0", "0"])[:3]
        return tuple(int(x) for x in parts)  # type: ignore[return-value]
    except (ValueError, AttributeError):
        return None


if merged_bytes:
    with st.expander("Aperçu du merged JSON", expanded=False):
        try:
            merged_data: dict[str, Any] = json.loads(merged_bytes.decode("utf-8"))
            meta = merged_data.get("meta") or {}
            assets = merged_data.get("assets") or {}
            signals = merged_data.get("signals") or []

            c1, c2, c3, c4 = st.columns(4)
            c1.metric("Version", meta.get("version", "N/A"))
            c2.metric("Assets", len(assets))
            c3.metric("Signaux", len(signals))
            scanners = meta.get("scanners_detected") or []
            c4.metric("Scanners", ", ".join(map(str, scanners))[:30] if scanners else "N/A")

            version = str(meta.get("version") or "")
            if not version:
                st.error("Version schema absente — merge_app.py obsolète ?")
            else:
                parsed = _parse_version(version)
                if parsed is None:
                    st.warning(f"Version schema non parseable : {version}")
                elif parsed < MIN_SCHEMA:
                    st.warning(
                        f"Version schema {version} < "
                        f"{'.'.join(map(str, MIN_SCHEMA))} — risque de désynchronisation"
                    )
                else:
                    st.success(f"Version schema {version} compatible")

            if assets:
                st.markdown(
                    "**Assets (top 5) :** " + ", ".join(list(assets.keys())[:5])
                )
        except json.JSONDecodeError as exc:
            st.error(f"JSON invalide : {exc}")
        except Exception as exc:  # noqa: BLE001
            st.error(f"Erreur lecture : {exc}")


# ════════════════════════════════════════════════════════════════════════════
# Exécution du pipeline
# ════════════════════════════════════════════════════════════════════════════
def _inputs_fingerprint(m: Optional[bytes], c: Optional[bytes]) -> str:
    h = hashlib.sha256()
    h.update(m or b"")
    h.update(b"|")
    h.update(c or b"")
    return h.hexdigest()


_cur_fp = _inputs_fingerprint(merged_bytes, calendar_bytes) if merged_file else None

if _cur_fp != st.session_state.get("report_fingerprint"):
    for k in ("report_html", "report_base_name", "report_pdf_bytes"):
        st.session_state.pop(k, None)

if st.button(
    "Générer le rapport",
    type="primary",
    use_container_width=True,
    disabled=merged_file is None,
):
    with st.spinner("Pipeline en cours..."):
        with tempfile.TemporaryDirectory() as tmpdir:
            merged_path = os.path.join(tmpdir, "merged.json")
            output_path = os.path.join(tmpdir, "report.html")
            pdf_path = os.path.join(tmpdir, "report.pdf")

            with open(merged_path, "wb") as f:
                f.write(merged_bytes or b"")

            kwargs: dict[str, Any] = {
                "merged_path": merged_path,
                "output_path": output_path,
                # Toujours fourni : sinon run_pipeline auto-nomme le PDF et
                # écrit dans le répertoire courant (read-only sur le Cloud).
                "pdf_path": pdf_path,
            }

            if calendar_bytes:
                calendar_path = os.path.join(tmpdir, "calendar.json")
                with open(calendar_path, "wb") as f:
                    f.write(calendar_bytes)
                kwargs["calendar_json_path"] = calendar_path

            try:
                html = run_pipeline(**kwargs)

                try:
                    _ga = (
                        json.loads((merged_bytes or b"{}").decode("utf-8"))
                        .get("meta", {})
                        .get("generated_at", "")
                    )
                    _rd = datetime.fromisoformat(
                        str(_ga).replace("Z", "+00:00")
                    ).strftime("%Y.%m.%d")
                except Exception:  # noqa: BLE001
                    _rd = datetime.now(timezone.utc).strftime("%Y.%m.%d")

                pdf_bytes: Optional[bytes] = None
                if os.path.exists(pdf_path):
                    with open(pdf_path, "rb") as f_pdf:
                        pdf_bytes = f_pdf.read()

                st.session_state["report_base_name"] = (
                    f"BLUESTAR FX Desk_Signal Report_{_rd}"
                )
                st.session_state["report_html"] = html
                st.session_state["report_pdf_bytes"] = pdf_bytes
                st.session_state["report_fingerprint"] = _cur_fp
                st.success("Rapport généré avec succès")
            except Exception as exc:  # noqa: BLE001
                st.error(f"Erreur pipeline : {exc}")
                st.exception(exc)
                st.stop()


# ════════════════════════════════════════════════════════════════════════════
# Affichage + téléchargements
# ════════════════════════════════════════════════════════════════════════════
if "report_html" in st.session_state:
    html = st.session_state["report_html"]
    _base_name = st.session_state.get(
        "report_base_name", "BLUESTAR FX Desk_Signal Report"
    )
    _pdf_data = st.session_state.get("report_pdf_bytes")

    tab_preview, tab_source = st.tabs(["Aperçu", "Source HTML"])

    with tab_preview:
        st_html(html, height=1800, scrolling=True)

    with tab_source:
        preview = html[:5000] + ("\n... (tronqué)" if len(html) > 5000 else "")
        st.code(preview, language="html")

    st.divider()

    col_dl1, col_dl2 = st.columns(2)

    with col_dl1:
        st.download_button(
            label="Télécharger HTML (calibré A4)",
            data=html.encode("utf-8"),
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
            st.info(
                "PDF natif non disponible (WeasyPrint absent de cet hébergement). "
                "Télécharge le HTML, ouvre-le, puis **Imprimer → Enregistrer en "
                "PDF** : marges 0, échelle 100 %, format A4 portrait. "
                "La feuille `@media print` du moteur fait le reste."
            )
else:
    st.info("Upload le fichier merged JSON pour lancer le pipeline.")
