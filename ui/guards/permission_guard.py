"""Guard puro que filtra entradas del sidebar según los permisos de la sesión.

Función pura (sin side-effects) para que sea testeable sin widgets Tk.

Decisión de arquitectura:
    - Este guard es DEFENSA PRIMARIA: un botón que el usuario no puede
      usar no se renderiza siquiera. Esto cumple la REGLA: "El menú
      solo muestra opciones que el usuario tiene permiso de ver".
    - La DEFENSA SECUNDARIA es ``@require_permission`` en los métodos
      del controller. Si un bug futuro del sidebar dejara una opción
      visible sin permiso, el click no renderizaría la vista.
"""

from __future__ import annotations

from typing import List, Sequence

from core.services.session import Session
from ui.menu_items import MenuItem


def filter_visible(items: Sequence[MenuItem], session: Session) -> List[MenuItem]:
    """Devuelve las entradas que la sesión actual tiene permiso de ver.

    Preserva el orden original — la UI decide cómo ordenar/agrupar.

    Args:
        items: Catálogo completo de entradas del sidebar.
        session: Sesión activa. No se acepta ``None`` aquí: si no hay
            sesión, el MainWindow ni siquiera debe instanciarse.

    Returns:
        Sublista con los ``MenuItem`` cuyos permisos coinciden con los
        de la sesión.
    """
    return [item for item in items if session.has_permission(item.permission)]
