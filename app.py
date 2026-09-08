"""
BLUESTAR ENGINE — Interface Streamlit v10
==========================================

Interface cloud-safe pour ENGINE.V10.py.

Déploiement Streamlit :
- aucune dépendance système obligatoire ;
- WeasyPrint est optionnel ;
- le moteur produit toujours un rapport HTML autonome calibré A4 ;
- si WeasyPrint est absent, le PDF est obtenu via l’impression navigateur.

Le fichier ENGINE.V10.py doit se trouver dans le même répertoire.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import platform
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from types import ModuleType
from typing import Any

import streamlit as st
from streamlit.components.v1 import html as st_html


# ════════════════════════════════════════════════════════════════════════════
# Configuration Streamlit
# Cette instruction doit rester la première commande Streamlit du fichier.
# ════════════════════════════════════════════════════════════════════════════

st.set_page_config(
    page_title="BLUESTAR ENGINE v10",
    page_icon="🔵",
    layout="wide",
    initial_sidebar_state="expanded",
)


# ════════════════════════════════════════════════════════════════════════════
# Constantes
# ════════════════════════════════════════════════════════════════════════════

APP_DIRECTORY = Path(__file__).resolve().parent
ENGINE_FILENAME = "ENGINE.V10.py"
ENGINE_MODULE_NAME = "bluestar_engine_v10"

MIN_MERGE_SCHEMA = (3, 4, 0)

REQUIRED_ENGINE_CALLABLES = (
    "run_pipeline",
    "render_report",
    "render_pdf",
)


# ════════════════════════════════════════════════════════════════════════════
# Helpers généraux
# ════════════════════════════════════════════════════════════════════════════

def _sha256_bytes(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _file_hash(path: Path) -> str:
    try:
        return _sha256_bytes(path.read_bytes())
    except OSError:
        return "unavailable"


def _parse_schema_version(value: Any) -> tuple[int, int, int] | None:
    """Convertit une version x.y.z en tuple comparable."""
    if value is None:
        return None

    try:
        raw_parts = str(value).strip().split(".")
        padded = (raw_parts + ["0", "0", "0"])[:3]
        return tuple(int(part) for part in padded)
    except (TypeError, ValueError):
        return None


def _decode_json_bytes(
    content: bytes,
    *,
    label: str,
) -> dict[str, Any]:
    """Décode et valide un document JSON racine de type objet."""
    if not content:
        raise ValueError(f"{label} est vide.")

    try:
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise ValueError(
            f"{label} n'est pas encodé en UTF-8."
        ) from exc

    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(
            f"{label} contient un JSON invalide à la ligne "
            f"{exc.lineno}, colonne {exc.colno} : {exc.msg}"
        ) from exc

    if not isinstance(data, dict):
        raise ValueError(
            f"{label} doit contenir un objet JSON à la racine."
        )

    return data


def _input_fingerprint(
    merged_content: bytes | None,
    calendar_content: bytes | None,
) -> str | None:
    if merged_content is None:
        return None

    digest = hashlib.sha256()
    digest.update(b"BLUESTAR-MERGED\0")
    digest.update(merged_content)
    digest.update(b"\0BLUESTAR-CALENDAR\0")
    digest.update(calendar_content or b"")
    return digest.hexdigest()


def _report_date_from_merged(merged_data: dict[str, Any]) -> str:
    """Retourne YYYY.MM.DD depuis meta.generated_at, sinon date UTC courante."""
    generated_at = (merged_data.get("meta") or {}).get("generated_at")

    if generated_at:
        try:
            parsed = datetime.fromisoformat(
                str(generated_at).replace("Z", "+00:00")
            )

            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)

            return parsed.astimezone(timezone.utc).strftime("%Y.%m.%d")
        except (TypeError, ValueError):
            pass

    return datetime.now(timezone.utc).strftime("%Y.%m.%d")


def _clear_report_state() -> None:
    for key in (
        "report_html",
        "report_base_name",
        "report_pdf_bytes",
        "report_fingerprint",
    ):
        st.session_state.pop(key, None)


# ════════════════════════════════════════════════════════════════════════════
# Chargement du moteur
# ════════════════════════════════════════════════════════════════════════════

@st.cache_resource(show_spinner=False)
def _load_engine(
    engine_path_string: str,
    source_hash: str,
) -> tuple[ModuleType | None, str | None]:
    """Charge ENGINE.V10.py dans un module isolé.

    source_hash fait partie de la clé du cache. Une modification du fichier
    invalide donc automatiquement la ressource.
    """
    del source_hash

    engine_path = Path(engine_path_string)

    if not engine_path.is_file():
        return None, (
            f"Fichier moteur introuvable : {engine_path.name}. "
            f"Il doit être placé à la racine du dépôt."
        )

    try:
        spec = importlib.util.spec_from_file_location(
            ENGINE_MODULE_NAME,
            engine_path,
        )

        if spec is None or spec.loader is None:
            return None, (
                f"Impossible de créer la spécification d'import pour "
                f"{engine_path.name}."
            )

        module = importlib.util.module_from_spec(spec)

        # Nécessaire pour certains mécanismes Python, notamment les dataclasses.
        sys.modules[ENGINE_MODULE_NAME] = module

        spec.loader.exec_module(module)

        module.__file_hash__ = _file_hash(engine_path)
        module.__loaded_at__ = datetime.now(timezone.utc)

        return module, None

    except Exception as exc:  # noqa: BLE001
        sys.modules.pop(ENGINE_MODULE_NAME, None)
        return None, f"{type(exc).__name__}: {exc}"


ENGINE_PATH = APP_DIRECTORY / ENGINE_FILENAME
ENGINE_HASH = _file_hash(ENGINE_PATH)

engine, engine_error = _load_engine(
    str(ENGINE_PATH),
    ENGINE_HASH,
)


# ════════════════════════════════════════════════════════════════════════════
# Validation de l’API moteur
# ════════════════════════════════════════════════════════════════════════════

engine_api_missing: list[str] = []

if engine is not None:
    engine_api_missing = [
        name
        for name in REQUIRED_ENGINE_CALLABLES
        if not callable(getattr(engine, name, None))
    ]

engine_version = (
    str(getattr(engine, "__version__", "inconnue"))
    if engine is not None
    else "indisponible"
)

has_native_pdf = bool(
    getattr(engine, "_HAS_WEASYPRINT", False)
) if engine is not None else False

native_pdf_error = (
    str(getattr(engine, "_WEASYPRINT_ERROR", "") or "")
    if engine is not None
    else ""
)


# ════════════════════════════════════════════════════════════════════════════
# Sidebar
# ════════════════════════════════════════════════════════════════════════════

with st.sidebar:
    st.markdown("## BLUESTAR SYSTEM")
    st.caption("FX Institutional Desk — Hybrid V4")

    if engine is None:
        st.error("Moteur non chargé")
    elif engine_api_missing:
        st.error("Moteur incomplet")
        st.code(
            "\n".join(engine_api_missing),
            language="text",
        )
    else:
        loaded_at = getattr(engine, "__loaded_at__", None)
        displayed_hash = getattr(
            engine,
            "__file_hash__",
            "unavailable",
        )

        st.success(f"Moteur : {ENGINE_FILENAME}")
        st.caption(f"Version : `{engine_version}`")
        st.caption(
            "Hash : `"
            + (
                displayed_hash[:12]
                if displayed_hash != "unavailable"
                else "inconnu"
            )
            + "`"
        )
        st.caption(
            "Chargé : `"
            + (
                loaded_at.strftime("%Y-%m-%d %H:%M UTC")
                if isinstance(loaded_at, datetime)
                else "inconnu"
            )
            + "`"
        )

    st.divider()
    st.markdown("### Rendu PDF")

    if has_native_pdf:
        st.success("Backend PDF natif actif")
    else:
        st.info(
            "Backend PDF natif indisponible.\n\n"
            "Le rapport HTML reste calibré A4. Télécharge-le, "
            "ouvre-le dans un navigateur, puis utilise "
            "**Imprimer → Enregistrer en PDF**."
        )

        if native_pdf_error:
            with st.expander("Détail backend PDF", expanded=False):
                st.code(native_pdf_error, language="text")

    st.divider()
    st.markdown("### Pipeline")
    st.markdown(
        "1. Validation du merged JSON\n"
        "2. Validation du calendrier\n"
        "3. Gates univers\n"
        "4. Facteurs F1 à F7\n"
        "5. Contradictions et conviction\n"
        "6. Preflight et diversification\n"
        "7. Rapport HTML/PDF"
    )

    st.divider()

    with st.expander("Environnement", expanded=False):
        st.caption(f"Python `{platform.python_version()}`")
        st.caption(f"Streamlit `{st.__version__}`")
        st.caption(f"Engine `{engine_version}`")
        st.caption(
            "PDF natif `"
            + ("actif" if has_native_pdf else "inactif")
            + "`"
        )

    if st.button(
        "Vider les caches",
        use_container_width=True,
    ):
        _clear_report_state()
        st.cache_resource.clear()
        st.cache_data.clear()
        st.rerun()


# ════════════════════════════════════════════════════════════════════════════
# Header et erreurs moteur
# ════════════════════════════════════════════════════════════════════════════

st.title(
    f"BLUESTAR ENGINE v{engine_version}"
    if engine is not None
    else "BLUESTAR ENGINE"
)

st.caption(
    "FX Institutional Desk — "
    "Hybrid Absolute/Cross-Sectional V4"
)

if engine is None:
    st.error("Le moteur n’a pas pu être chargé.")

    if engine_error:
        st.code(engine_error, language="text")

    st.info(
        f"Vérifie que `{ENGINE_FILENAME}` est présent à la racine "
        "du dépôt et que toutes ses dépendances figurent dans "
        "`requirements.txt`."
    )

    st.stop()

if engine_api_missing:
    st.error(
        f"`{ENGINE_FILENAME}` est incomplet ou a été tronqué."
    )
    st.code(
        "Fonctions obligatoires absentes :\n"
        + "\n".join(
            f"- {name}"
            for name in engine_api_missing
        ),
        language="text",
    )
    st.info(
        "Le moteur doit contenir `run_pipeline`, `render_report` "
        "et `render_pdf`."
    )
    st.stop()

run_pipeline = getattr(engine, "run_pipeline")


# ════════════════════════════════════════════════════════════════════════════
# Uploads
# ════════════════════════════════════════════════════════════════════════════

upload_column_1, upload_column_2 = st.columns(2)

with upload_column_1:
    merged_file = st.file_uploader(
        "Merged JSON",
        type=["json", "txt"],
        key="merged_json_upload",
        help=(
            "Fichier bluestar_merged_*.json. "
            "Le schéma recommandé est au minimum 3.4.0."
        ),
    )

with upload_column_2:
    calendar_file = st.file_uploader(
        "Calendar JSON — optionnel",
        type=["json", "txt"],
        key="calendar_json_upload",
        help=(
            "Calendrier économique wrapper ou CalendarData natif. "
            "En son absence, F7 MACRO fonctionne en fail-closed."
        ),
    )

merged_bytes = (
    merged_file.getvalue()
    if merged_file is not None
    else None
)

calendar_bytes = (
    calendar_file.getvalue()
    if calendar_file is not None
    else None
)


# ════════════════════════════════════════════════════════════════════════════
# Validation et aperçu des entrées
# ════════════════════════════════════════════════════════════════════════════

merged_data: dict[str, Any] | None = None
calendar_data: dict[str, Any] | None = None
input_errors: list[str] = []
input_warnings: list[str] = []

if merged_bytes is not None:
    try:
        merged_data = _decode_json_bytes(
            merged_bytes,
            label="Merged JSON",
        )

        meta = merged_data.get("meta")
        assets = merged_data.get("assets")
        signals = merged_data.get("signals")

        if not isinstance(meta, dict):
            input_errors.append(
                "Merged JSON : le champ `meta` est absent ou invalide."
            )
            meta = {}

        if not isinstance(assets, dict):
            input_errors.append(
                "Merged JSON : le champ `assets` doit être un objet."
            )
            assets = {}

        if not assets:
            input_errors.append(
                "Merged JSON : aucun actif n’est disponible."
            )

        if signals is None:
            signals = []
        elif not isinstance(signals, list):
            input_warnings.append(
                "Merged JSON : le champ `signals` n’est pas une liste."
            )
            signals = []

        schema_raw = meta.get("version")
        schema_version = _parse_schema_version(schema_raw)

        if schema_version is None:
            input_errors.append(
                "Merged JSON : `meta.version` est absent ou non parseable."
            )
        elif schema_version < MIN_MERGE_SCHEMA:
            input_errors.append(
                f"Merged JSON : schéma {schema_raw} inférieur au minimum "
                f"{'.'.join(map(str, MIN_MERGE_SCHEMA))}."
            )

        generated_at = meta.get("generated_at")
        if generated_at is None:
            input_warnings.append(
                "Merged JSON : `meta.generated_at` est absent."
            )

    except ValueError as exc:
        input_errors.append(str(exc))

if calendar_bytes is not None:
    try:
        calendar_data = _decode_json_bytes(
            calendar_bytes,
            label="Calendar JSON",
        )

        has_wrapper_events = isinstance(
            calendar_data.get("events_engine"),
            list,
        ) or isinstance(
            calendar_data.get("events"),
            list,
        )

        if not has_wrapper_events:
            input_warnings.append(
                "Calendar JSON : aucune liste `events_engine` ou `events` "
                "n’a été détectée. Le moteur tentera le format natif."
            )

    except ValueError as exc:
        input_errors.append(str(exc))


if merged_data is not None:
    with st.expander(
        "Aperçu et validation des données",
        expanded=True,
    ):
        meta = (
            merged_data.get("meta")
            if isinstance(merged_data.get("meta"), dict)
            else {}
        )
        assets = (
            merged_data.get("assets")
            if isinstance(merged_data.get("assets"), dict)
            else {}
        )
        signals = (
            merged_data.get("signals")
            if isinstance(merged_data.get("signals"), list)
            else []
        )

        scanners = meta.get("scanners_detected")
        if not isinstance(scanners, list):
            scanners = []

        metric_1, metric_2, metric_3, metric_4 = st.columns(4)

        metric_1.metric(
            "Schéma",
            str(meta.get("version") or "N/A"),
        )
        metric_2.metric(
            "Actifs",
            len(assets),
        )
        metric_3.metric(
            "Signaux",
            len(signals),
        )
        metric_4.metric(
            "Scanners",
            len(scanners),
        )

        if assets:
            st.markdown(
                "**Premiers actifs :** "
                + ", ".join(
                    list(map(str, assets.keys()))[:10]
                )
            )

        if calendar_data is not None:
            metadata = calendar_data.get("metadata")
            if not isinstance(metadata, dict):
                metadata = {}

            events = calendar_data.get("events_engine")
            if not isinstance(events, list):
                events = calendar_data.get("events")
            if not isinstance(events, list):
                events = []

            st.markdown(
                f"**Calendrier :** {len(events)} événement(s) détecté(s)"
            )

            generated_calendar = metadata.get("generated_at_utc")
            if generated_calendar:
                st.caption(
                    f"Généré : `{generated_calendar}`"
                )

for warning in input_warnings:
    st.warning(warning)

for error in input_errors:
    st.error(error)


# ════════════════════════════════════════════════════════════════════════════
# Gestion de l’état du rapport
# ════════════════════════════════════════════════════════════════════════════

current_fingerprint = _input_fingerprint(
    merged_bytes,
    calendar_bytes,
)

stored_fingerprint = st.session_state.get(
    "report_fingerprint"
)

if (
    stored_fingerprint is not None
    and current_fingerprint != stored_fingerprint
):
    _clear_report_state()


# ════════════════════════════════════════════════════════════════════════════
# Exécution
# ════════════════════════════════════════════════════════════════════════════

generation_disabled = (
    merged_bytes is None
    or bool(input_errors)
)

generate_clicked = st.button(
    "Générer le rapport",
    type="primary",
    use_container_width=True,
    disabled=generation_disabled,
)

if generate_clicked:
    assert merged_bytes is not None
    assert merged_data is not None

    with st.spinner(
        "Scoring, contrôles de risque et génération du rapport..."
    ):
        try:
            with tempfile.TemporaryDirectory(
                prefix="bluestar_"
            ) as temporary_directory:
                temporary_path = Path(temporary_directory)

                merged_path = temporary_path / "merged.json"
                calendar_path = temporary_path / "calendar.json"
                output_path = temporary_path / "report.html"
                pdf_path = temporary_path / "report.pdf"

                merged_path.write_bytes(merged_bytes)

                pipeline_arguments: dict[str, Any] = {
                    "merged_path": str(merged_path),
                    "output_path": str(output_path),
                    "pdf_path": str(pdf_path),
                }

                if calendar_bytes is not None:
                    calendar_path.write_bytes(calendar_bytes)
                    pipeline_arguments["calendar_json_path"] = str(
                        calendar_path
                    )

                report_html = run_pipeline(
                    **pipeline_arguments
                )

                if (
                    not isinstance(report_html, str)
                    or not report_html.strip()
                ):
                    raise RuntimeError(
                        "Le moteur n’a retourné aucun HTML."
                    )

                normalized_html = report_html.lower()

                if (
                    "<html" not in normalized_html
                    or "</html>" not in normalized_html
                ):
                    raise RuntimeError(
                        "Le résultat du moteur n’est pas un document "
                        "HTML complet."
                    )

                report_pdf_bytes: bytes | None = None

                if pdf_path.is_file() and pdf_path.stat().st_size > 0:
                    report_pdf_bytes = pdf_path.read_bytes()

                    if not report_pdf_bytes.startswith(b"%PDF-"):
                        report_pdf_bytes = None

                report_date = _report_date_from_merged(
                    merged_data
                )

                st.session_state["report_base_name"] = (
                    f"BLUESTAR FX Desk_Signal Report_{report_date}"
                )
                st.session_state["report_html"] = report_html
                st.session_state["report_pdf_bytes"] = (
                    report_pdf_bytes
                )
                st.session_state["report_fingerprint"] = (
                    current_fingerprint
                )

            st.success("Rapport généré avec succès.")

        except Exception as exc:  # noqa: BLE001
            _clear_report_state()

            st.error(
                f"Erreur pipeline : {type(exc).__name__}: {exc}"
            )
            st.exception(exc)


# ════════════════════════════════════════════════════════════════════════════
# Affichage et téléchargements
# ════════════════════════════════════════════════════════════════════════════

report_html_state = st.session_state.get("report_html")

if isinstance(report_html_state, str) and report_html_state:
    report_base_name = str(
        st.session_state.get(
            "report_base_name",
            "BLUESTAR FX Desk_Signal Report",
        )
    )

    report_pdf_state = st.session_state.get(
        "report_pdf_bytes"
    )

    preview_tab, source_tab = st.tabs(
        ["Aperçu", "Diagnostic HTML"]
    )

    with preview_tab:
        st_html(
            report_html_state,
            height=1800,
            scrolling=True,
        )

    with source_tab:
        st.metric(
            "Taille HTML",
            f"{len(report_html_state):,} caractères".replace(",", " "),
        )
        st.code(
            report_html_state[:5000]
            + (
                "\n\n<!-- aperçu tronqué -->"
                if len(report_html_state) > 5000
                else ""
            ),
            language="html",
        )

    st.divider()

    download_column_1, download_column_2 = st.columns(2)

    with download_column_1:
        st.download_button(
            label="Télécharger le rapport HTML A4",
            data=report_html_state.encode("utf-8"),
            file_name=f"{report_base_name}.html",
            mime="text/html",
            use_container_width=True,
        )

    with download_column_2:
        if (
            isinstance(report_pdf_state, bytes)
            and report_pdf_state.startswith(b"%PDF-")
        ):
            st.download_button(
                label="Télécharger le rapport PDF",
                data=report_pdf_state,
                file_name=f"{report_base_name}.pdf",
                mime="application/pdf",
                use_container_width=True,
            )
        else:
            st.info(
                "PDF natif indisponible sur cet environnement. "
                "Télécharge le HTML, ouvre-le dans un navigateur, "
                "puis utilise **Imprimer → Enregistrer en PDF**, "
                "format A4, échelle 100 %."
            )

elif merged_bytes is None:
    st.info(
        "Charge un merged JSON pour lancer le pipeline."
    )

elif input_errors:
    st.info(
        "Corrige les erreurs de validation avant de lancer le pipeline."
    )

