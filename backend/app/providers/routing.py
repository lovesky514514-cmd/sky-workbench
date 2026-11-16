"""按能力使用用户已配置且启用的模型，不更改全局设置或自动切换供应商。"""
from copy import deepcopy


class RoleSettings:
    def __init__(self, store, provider, model):
        self.store, self.provider, self.model = store, provider, model

    def load(self):
        settings = deepcopy(self.store.load())
        provider = settings['providers'].get(self.provider)
        if provider is None or provider.get('enabled') is not True:
            raise ValueError('role_provider_not_enabled')
        settings['active_provider'] = self.provider
        if self.model:
            provider['model'] = self.model
        return settings

    def secret(self, name, base_url=None):
        return self.store.secret(name, base_url)


def settings_for_role(store, role):
    routes = store.load().get('research', {}).get('role_models', {})
    route = routes.get(role)
    if route is None:
        return None
    if not isinstance(route, dict) or set(route) - {'provider', 'model'} or not isinstance(route.get('provider'), str):
        raise ValueError('invalid_role_model_route')
    model = route.get('model', '')
    if not isinstance(model, str) or len(model) > 200 or any(ord(char) < 32 for char in model):
        raise ValueError('invalid_role_model_name')
    settings = RoleSettings(store, route['provider'], model)
    settings.load()
    return settings
