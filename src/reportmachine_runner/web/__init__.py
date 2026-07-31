"""Optional localhost Web control plane for ReportMachine.

This package deliberately keeps Flask and Waitress out of import-time dependencies.
Framework-neutral contracts, state, status, and artifact inspection remain usable in the
core installation; :func:`reportmachine_runner.web.app.create_app` performs the optional
dependency check only when a Web application is requested.
"""

from .contracts import Action, ActionRequest, ContractError, build_runner_argv

__all__ = [
    "Action",
    "ActionRequest",
    "ContractError",
    "build_runner_argv",
]
