"""Streamlit UI surfaces. Components that touch `st.*` live here.

Pure data-prep is in `tokenscope.analytics`; subprocess + cache is in
`tokenscope.data`. Nothing in `tokenscope.ui` should be imported by the
non-UI layers.
"""
