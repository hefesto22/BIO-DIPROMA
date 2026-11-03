"""Componente reutilizable ``CatalogoTab``.

Tab genérico para administrar un catálogo con forma (``id``, ``nombre``,
``is_active``). Departamentos y cargos comparten exactamente la misma UX
— crear, renombrar, archivar, reactivar — así que este componente se
parametriza por callables en vez de duplicarse.

No sabe nada de BD ni de servicios: recibe callables desde el controller.
La vista contenedora (``ConfiguracionView``) es la encargada de inyectar
los callables que apuntan a cada método del ``ConfiguracionController``.

Diseño visual:

    ┌───────────────────────────────────────────────────────┐
    │  [🔲 Ver archivados]                      [Refrescar]  │
    ├───────────────────────────────────────────────────────┤
    │  Scrollable frame:                                    │
    │    · Nombre A          [Renombrar] [Archivar]         │
    │    · Nombre B          [Renombrar] [Archivar]         │
    │    · Nombre C (gris)   [Reactivar]                    │
    ├───────────────────────────────────────────────────────┤
    │  Nuevo:  [_____________________]  [Agregar]           │
    └───────────────────────────────────────────────────────┘

Errores de negocio (``DuplicateNombreError``, ``CatalogoInUseError``,
``MissingRequiredFieldError``) se capturan en cada handler y se muestran
con ``messagebox.showerror`` en español.
"""

from __future__ import annotations

import logging
from tkinter import messagebox
from typing import Callable, Optional, Protocol, Sequence

import customtkinter as ctk

from core.services.errors import (
    CatalogoInUseError,
    DuplicateNombreError,
    MissingRequiredFieldError,
)


class CatalogItem(Protocol):
    """Contrato estructural mínimo para una fila del catálogo."""

    @property
    def id(self) -> Optional[int]:
        """PK en la tabla."""

    @property
    def nombre(self) -> str:
        """Nombre visible al usuario."""

    @property
    def is_active(self) -> bool:
        """Flag de soft-delete."""


# Callables inyectados desde el controller — usamos Sequence (covariante)
# porque ``List[Departamento]`` no sería subtipo de ``List[CatalogItem]``.
ListFn = Callable[[bool], Sequence[CatalogItem]]
CreateFn = Callable[[str], CatalogItem]
RenameFn = Callable[[int, str], None]
ArchiveFn = Callable[[int], None]
UnarchiveFn = Callable[[int], None]


class CatalogoTab(ctk.CTkFrame):
    """Tab genérico para administrar un catálogo simple."""

    def __init__(
        self,
        master: ctk.CTkBaseClass,
        title_singular: str,
        list_fn: ListFn,
        create_fn: CreateFn,
        rename_fn: RenameFn,
        archive_fn: ArchiveFn,
        unarchive_fn: UnarchiveFn,
    ) -> None:
        """Construye el tab.

        Args:
            master: Contenedor Tk donde se monta.
            title_singular: "Departamento" o "Cargo" — usado en prompts
                y mensajes de confirmación.
            list_fn: ``fn(solo_activos)`` → lista de items.
            create_fn: ``fn(nombre)`` → item creado con id asignado.
            rename_fn: ``fn(id, nuevo_nombre)``.
            archive_fn: ``fn(id)``.
            unarchive_fn: ``fn(id)``.
        """
        super().__init__(master, corner_radius=0, fg_color="transparent")
        self._title_singular = title_singular
        self._list_fn = list_fn
        self._create_fn = create_fn
        self._rename_fn = rename_fn
        self._archive_fn = archive_fn
        self._unarchive_fn = unarchive_fn
        self._log = logging.getLogger(f"{self.__class__.__name__}[{title_singular}]")

        # Estado UI — toggle "Ver archivados" arranca apagado.
        self._ver_archivados_var = ctk.BooleanVar(value=False)

        self._construir_ui()
        self._refrescar()

    # ── Construcción del árbol de widgets ─────────────────────────────────

    def _construir_ui(self) -> None:
        """Arma la barra superior, la lista y la barra inferior."""
        # Grid raíz: toolbar, lista (expande), form.
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)

        self._toolbar = self._construir_toolbar()
        self._toolbar.grid(row=0, column=0, sticky="ew", padx=16, pady=(16, 8))

        self._lista = ctk.CTkScrollableFrame(self, fg_color=("gray95", "gray15"))
        self._lista.grid(row=1, column=0, sticky="nsew", padx=16, pady=0)
        self._lista.grid_columnconfigure(0, weight=1)

        self._form = self._construir_form()
        self._form.grid(row=2, column=0, sticky="ew", padx=16, pady=(8, 16))

    def _construir_toolbar(self) -> ctk.CTkFrame:
        """Checkbox de archivados + botón refrescar."""
        bar = ctk.CTkFrame(self, fg_color="transparent")
        bar.grid_columnconfigure(0, weight=1)

        chk = ctk.CTkCheckBox(
            bar,
            text="Ver archivados",
            variable=self._ver_archivados_var,
            command=self._refrescar,
        )
        chk.grid(row=0, column=0, sticky="w")

        btn_refresh = ctk.CTkButton(
            bar,
            text="Refrescar",
            width=100,
            command=self._refrescar,
        )
        btn_refresh.grid(row=0, column=1, sticky="e")

        return bar

    def _construir_form(self) -> ctk.CTkFrame:
        """Entry + botón para crear un nuevo item."""
        form = ctk.CTkFrame(self, fg_color="transparent")
        form.grid_columnconfigure(1, weight=1)

        label = ctk.CTkLabel(form, text=f"Nuevo {self._title_singular.lower()}:")
        label.grid(row=0, column=0, padx=(0, 8), sticky="w")

        self._entry_nuevo = ctk.CTkEntry(form, placeholder_text="Escriba el nombre")
        self._entry_nuevo.grid(row=0, column=1, sticky="ew", padx=(0, 8))
        self._entry_nuevo.bind("<Return>", lambda _e: self._on_agregar())

        btn_agregar = ctk.CTkButton(form, text="Agregar", width=100, command=self._on_agregar)
        btn_agregar.grid(row=0, column=2, sticky="e")

        return form

    # ── Render de la lista ────────────────────────────────────────────────

    def _refrescar(self) -> None:
        """Lee del controller y vuelve a pintar toda la lista."""
        # Limpiar filas previas.
        for hijo in self._lista.winfo_children():
            hijo.destroy()

        solo_activos = not self._ver_archivados_var.get()
        try:
            items = self._list_fn(solo_activos)
        except Exception:  # noqa: BLE001 — último recurso, log + aviso
            self._log.exception("Error listando %s", self._title_singular)
            messagebox.showerror(
                "Error",
                f"No se pudo cargar la lista de {self._title_singular.lower()}s.",
            )
            return

        if not items:
            self._pintar_vacio()
            return

        for idx, item in enumerate(items):
            self._pintar_fila(idx, item)

    def _pintar_vacio(self) -> None:
        """Muestra un placeholder cuando la lista está vacía."""
        msg = ctk.CTkLabel(
            self._lista,
            text=f"No hay {self._title_singular.lower()}s para mostrar.",
            text_color=("gray40", "gray60"),
            font=ctk.CTkFont(size=12, slant="italic"),
        )
        msg.grid(row=0, column=0, sticky="w", padx=12, pady=12)

    def _pintar_fila(self, idx: int, item: CatalogItem) -> None:
        """Dibuja una fila para un item activo o archivado."""
        fila = ctk.CTkFrame(self._lista, fg_color=("gray90", "gray20"))
        fila.grid(row=idx, column=0, sticky="ew", padx=4, pady=2)
        fila.grid_columnconfigure(0, weight=1)

        texto = item.nombre if item.is_active else f"{item.nombre}  (archivado)"
        color = ("gray10", "gray90") if item.is_active else ("gray45", "gray55")
        label = ctk.CTkLabel(fila, text=texto, text_color=color, anchor="w")
        label.grid(row=0, column=0, sticky="ew", padx=12, pady=8)

        if item.id is None:
            return  # Defensa: item sin id no se opera (imposible en la práctica).

        if item.is_active:
            btn_rename = ctk.CTkButton(
                fila,
                text="Renombrar",
                width=100,
                command=lambda i=item: self._on_rename(i),
            )
            btn_rename.grid(row=0, column=1, padx=4, pady=4)

            btn_archive = ctk.CTkButton(
                fila,
                text="Archivar",
                width=100,
                fg_color=("gray65", "gray35"),
                hover_color=("gray50", "gray45"),
                command=lambda i=item: self._on_archive(i),
            )
            btn_archive.grid(row=0, column=2, padx=4, pady=4)
        else:
            btn_unarchive = ctk.CTkButton(
                fila,
                text="Reactivar",
                width=100,
                command=lambda i=item: self._on_unarchive(i),
            )
            btn_unarchive.grid(row=0, column=1, padx=4, pady=4)

    # ── Handlers ──────────────────────────────────────────────────────────

    def _on_agregar(self) -> None:
        """Click en el botón Agregar del form inferior."""
        nombre = self._entry_nuevo.get().strip()
        if not nombre:
            messagebox.showwarning(
                "Campo requerido",
                f"Escriba el nombre del {self._title_singular.lower()}.",
            )
            return
        try:
            creado = self._create_fn(nombre)
        except MissingRequiredFieldError as exc:
            messagebox.showwarning("Campo requerido", str(exc))
            return
        except DuplicateNombreError as exc:
            messagebox.showerror("Nombre duplicado", str(exc))
            return
        except Exception:  # noqa: BLE001
            self._log.exception("Error creando %s", self._title_singular)
            messagebox.showerror(
                "Error",
                f"No se pudo crear el {self._title_singular.lower()}.",
            )
            return
        self._log.info("%s creado: id=%s", self._title_singular, creado.id)
        self._entry_nuevo.delete(0, "end")
        self._refrescar()

    def _on_rename(self, item: CatalogItem) -> None:
        """Click en el botón Renombrar de una fila."""
        dialog = ctk.CTkInputDialog(
            text=f'Nuevo nombre para "{item.nombre}":',
            title=f"Renombrar {self._title_singular.lower()}",
        )
        nuevo = dialog.get_input()
        if nuevo is None:
            return  # Cancelado por el usuario.
        nuevo_strip = nuevo.strip()
        if not nuevo_strip:
            messagebox.showwarning("Campo requerido", "El nombre no puede estar vacío.")
            return
        assert item.id is not None  # Filtrado en _pintar_fila.
        try:
            self._rename_fn(item.id, nuevo_strip)
        except MissingRequiredFieldError as exc:
            messagebox.showwarning("Campo requerido", str(exc))
            return
        except DuplicateNombreError as exc:
            messagebox.showerror("Nombre duplicado", str(exc))
            return
        except Exception:  # noqa: BLE001
            self._log.exception("Error renombrando %s id=%s", self._title_singular, item.id)
            messagebox.showerror(
                "Error",
                f"No se pudo renombrar el {self._title_singular.lower()}.",
            )
            return
        self._refrescar()

    def _on_archive(self, item: CatalogItem) -> None:
        """Click en el botón Archivar de una fila."""
        assert item.id is not None
        confirmado = messagebox.askyesno(
            f"Archivar {self._title_singular.lower()}",
            (
                f'¿Archivar "{item.nombre}"?\n\n'
                "No se borrará, pero dejará de estar disponible para "
                "nuevos empleados."
            ),
        )
        if not confirmado:
            return
        try:
            self._archive_fn(item.id)
        except CatalogoInUseError as exc:
            messagebox.showerror("No se puede archivar", str(exc))
            return
        except Exception:  # noqa: BLE001
            self._log.exception("Error archivando %s id=%s", self._title_singular, item.id)
            messagebox.showerror(
                "Error",
                f"No se pudo archivar el {self._title_singular.lower()}.",
            )
            return
        self._refrescar()

    def _on_unarchive(self, item: CatalogItem) -> None:
        """Click en el botón Reactivar de una fila archivada."""
        assert item.id is not None
        try:
            self._unarchive_fn(item.id)
        except Exception:  # noqa: BLE001
            self._log.exception("Error reactivando %s id=%s", self._title_singular, item.id)
            messagebox.showerror(
                "Error",
                f"No se pudo reactivar el {self._title_singular.lower()}.",
            )
            return
        self._refrescar()
