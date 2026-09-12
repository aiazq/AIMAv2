with settings_col:
        with st.popover("⚙️ Settings"):
            st.markdown("### API & Model Configuration")

            # Scoped CSS to permanently strip password-reveal buttons/eyes
            st.markdown(
                """
                <style>
                /* Remove Streamlit/browser password reveal toggles */
                button[aria-label="Show password text"],
                button[aria-label="Hide password text"],
                input[type="password"]::-ms-reveal,
                input[type="password"]::-ms-clear {
                    display: none !important;
                    visibility: hidden !important;
                    pointer-events: none !important;
                }
                </style>
                """,
                unsafe_allow_html=True,
            )

            # Masked write-only API key field
            has_key = bool(st.session_state.get("api_key"))
            status_indicator = "🟢 Key is securely set" if has_key else "🔴 No key configured"
            st.caption(f"Status: **{status_indicator}**")

            new_key_input = st.text_input(
                "Update Gemini API Key:",
                value="",
                type="password",
                placeholder="Paste new key to set/replace..." if not has_key else "•••••••••••••••• (Leave blank to keep)",
                help="Once entered, the key cannot be inspected or toggled visible.",
            )

            # Only update session state if a non-empty string is typed
            if new_key_input.strip():
                st.session_state["api_key"] = new_key_input.strip()
                st.rerun()

            # Model Name Input (remains configurable & visible)
            current_model = st.session_state.get("model_name", DEFAULT_MODEL)
            new_model = st.text_input("Model Endpoint:", value=current_model)
            if new_model != current_model:
                st.session_state["model_name"] = new_model

            st.caption("Settings persist for the active session and are never echoed.")
