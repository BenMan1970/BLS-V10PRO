import streamlit as st

st.set_page_config(page_title="Test Minimal", layout="wide")

st.title("Test de déploiement minimal")
st.success("Si tu vois cette page, Streamlit Cloud fonctionne parfaitement. Le problème vient donc du fichier ENGINE.")
st.info("Le problème précédent (boucle infinie ou crash silencieux) vient obligatoirement du fichier ENGINE.V9.py ou V10.py.")
