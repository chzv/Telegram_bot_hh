# app/core/compat.py
import builtins
from app.core.config import _get as __cfg_get, _get_list as __cfg_get_list
builtins._get = __cfg_get
builtins._get_list = __cfg_get_list
