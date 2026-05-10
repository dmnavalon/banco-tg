from __future__ import annotations


class ServiceError(Exception):
    """Base de errores de la capa de servicios. Permite a los handlers (bot
    o API) distinguir errores de dominio de excepciones genéricas."""


class MovementNotFound(ServiceError):
    def __init__(self, mov_id: str):
        super().__init__(f"Movimiento {mov_id} no existe")
        self.mov_id = mov_id


class InvalidTransition(ServiceError):
    def __init__(self, mov_id: str, current: str, attempted: str):
        super().__init__(
            f"Transición inválida en {mov_id}: review_status={current} no permite {attempted}"
        )
        self.mov_id = mov_id
        self.current = current
        self.attempted = attempted


class VersionConflict(ServiceError):
    def __init__(self, mov_id: str, expected: int, current: int, current_doc: dict | None = None):
        super().__init__(
            f"Conflicto de versión en {mov_id}: esperaba {expected}, actual {current}"
        )
        self.mov_id = mov_id
        self.expected = expected
        self.current = current
        self.current_doc = current_doc or {}


class ValidationError(ServiceError):
    """Para inputs inválidos (ej. ignore sin reason)."""
