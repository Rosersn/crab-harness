"""E2B sandbox integration for multi-tenant platform.

Provides E2BSandboxProvider (SandboxProvider ABC) and E2BSandbox (Sandbox ABC)
for running user code in isolated E2B cloud VMs.
"""

from crab_platform.sandbox.cleaner import SandboxCleaner
from crab_platform.sandbox.e2b_sandbox import E2BSandbox
from crab_platform.sandbox.e2b_sandbox_provider import E2BSandboxProvider
from crab_platform.sandbox.file_injector import inject_thread_uploads

__all__ = [
    "E2BSandbox",
    "E2BSandboxProvider",
    "SandboxCleaner",
    "inject_thread_uploads",
]
