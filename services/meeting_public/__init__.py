"""Public, meeting-only server for internet meetings (reached through Tailscale Funnel). Every request is untrusted.

It imports only ``services.meeting_public.gateway`` (and through it ``services.api.meeting_core`` and the database
models), never clinician route modules. See docs/INTERNET_MEETINGS_PLAN.md.
"""
