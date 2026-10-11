# SPDX-License-Identifier: MPL-2.0
# Load Official extensions when the private bundle is available.
import importlib
import importlib.util


def private_bundle_enabled():
    from core.flag import is_official
    return is_official() and importlib.util.find_spec("rennsproject") is not None


def private_features_available():
    return private_bundle_enabled()


class _PublicAppFeatures:
    def _init_authorization(self):
        pass

    def _init_account_features(self):
        self.account = {}
        self.profile_provider = None

    def _init_update_features(self):
        pass

    def _build_profile_card(self, content_layout):
        pass

    def _build_activation_card(self, content_layout):
        pass

    def _refresh_settings_badge(self):

        pass

    def _start_account_features(self):
        pass

    def _collect_profile_endpoints(self):
        pass

    def _prepare_account_page_transition(self, previous_page, page):
        return []

    def _start_account_page_transition(self):
        pass

    def _add_account_footer(self, layout):
        pass

    def _stop_update_features(self):
        pass

    def _stop_profile_card_transition(self):
        pass

    def _prepare_account_startup(self, on_ready):
        return False

    def _restore_account_startup(self):
        pass


class _PublicMainFeatures:
    def _init_private_toolbar(self, top_bar_layout):
        pass

    def _add_private_actions(self):
        pass

    def _start_private_features(self):
        pass

    def _stop_private_features(self):
        pass


def __getattr__(name):
    if name == "AppFeatures":
        if private_bundle_enabled():
            return importlib.import_module("rennsproject.app_ui").AppFeatures
        return _PublicAppFeatures
    if name == "MainFeatures":
        if private_bundle_enabled():
            return importlib.import_module("rennsproject.main_ui").MainFeatures
        return _PublicMainFeatures
    raise AttributeError(name)


def _run_private_cli(name, *args, **kwargs):
    if not private_features_available():
        return False, "This feature requires the OFCL edition bundle."
    module = importlib.import_module("rennsproject.core.rak_automation_cli")
    return getattr(module, name)(*args, **kwargs)


def run_automation_cli(*args, **kwargs):
    return _run_private_cli("run_automation_cli", *args, **kwargs)


def run_compare_cli(*args, **kwargs):
    return _run_private_cli("run_compare_cli", *args, **kwargs)


def official_web_search(query):
    if not private_features_available():
        return {"ok": False, "error": "This feature requires the OFCL edition bundle."}
    module = importlib.import_module("rennsproject.server_tools")
    return module.web_search(query)


def append_private_security_checks(outcomes):
    if private_features_available():
        module = importlib.import_module("rennsproject.security_checks")
        module.append_security_checks(outcomes)
