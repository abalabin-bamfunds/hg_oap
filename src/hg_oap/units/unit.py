import operator

from abc import abstractmethod
from collections import defaultdict
from dataclasses import dataclass, field
from decimal import Decimal
from functools import reduce
from typing import Tuple, ForwardRef, TypeVar, ClassVar

from hg_oap.units.dimension import Dimension
from hg_oap.utils.exprclass import ExprClass
from hg_oap.units.unit_system import UnitSystem

NUMBER = TypeVar('NUMBER', int, float, Decimal)


@dataclass(frozen=True, kw_only=True, init=False)
class Unit(ExprClass):
    name: str = None
    dimension: Dimension

    def __str__(self):
        return self.name

    def __hash__(self):
        return id(self)  # units are singletons within a UnitSystem by construction so this is safe

    def __rmul__(self, value):
        if isinstance(value, (int, float, Decimal)):
            from hg_oap.units.quantity import Quantity
            return Quantity(value, self)

        return NotImplemented

    def _to_components(self, power=1):
        return ((self, power),)

    def convert(self, value: NUMBER, to: 'Unit') -> NUMBER:
        if to is self:
            return value

        if self.dimension is to.dimension:
            return self._do_convert(value, to)
        elif conversion_factor := UnitSystem.instance().conversion_factor(to.dimension/self.dimension):
            converted_value = value * type(value)(conversion_factor.value)
            converted_units = self * conversion_factor.unit
            return converted_units.convert(converted_value, to)
        else:
            raise ValueError(f"cannot convert {self} to {to} and no conversion factor for {to.dimension/self.dimension}")

    @abstractmethod
    def _do_convert(self, value: NUMBER, to: 'Unit') -> NUMBER: ...

    _is_multiplicative: ClassVar[bool] = True


UNIT = TypeVar("UNIT", bound=Unit)


@dataclass(frozen=True, kw_only=True, init=False)
class PrimaryUnit(Unit):
    ratio: Decimal = Decimal(1)

    def __new__(cls, name=None, dimension: Dimension = None):
        if d := UnitSystem.instance().__primary_units__.get(dimension):
            assert d.dimension is dimension
            return d

        n = super().__new__(cls)
        object.__setattr__(n, 'dimension', dimension)
        if name:
            object.__setattr__(n, 'name', name)

        UnitSystem.instance().__primary_units__[dimension] = n
        return n

    def __pow__(self, power, modulo=None):
        return ComplexUnit(components=((self, power),))

    def __mul__(self, other):
        if isinstance(other, (PrimaryUnit, DerivedUnit, ComplexUnit)):
            components = defaultdict(int, other._to_components())
        else:
            raise ValueError(f"cannot multiply {self} and {other}")

        components[self] += 1
        return ComplexUnit(components=tuple(components.items()))

    def __truediv__(self, other):
        if isinstance(other, (PrimaryUnit, DerivedUnit, ComplexUnit)):
            components = defaultdict(int, other._to_components(-1))
        else:
            raise ValueError(f"cannot divide {self} and {other}")

        components[self] += 1
        return ComplexUnit(components=tuple(components.items()))

    def _do_convert(self, value: NUMBER, to: 'Unit') -> NUMBER:
        if isinstance(to, OffsetDerivedUnit):
            return value / type(value)(to.ratio) - type(value)(to.offset)
        elif isinstance(to, DerivedUnit):
            return value / type(value)(to.ratio)

        assert False, f'conversion from {self} to {to} is not supported'


@dataclass(frozen=True, kw_only=True, init=False)
class DerivedUnit(Unit):
    primary_unit: Unit
    ratio: Decimal
    dimension: Dimension = lambda s: s.primary_unit.dimension
    name: str = lambda s: f"{s.ratio}*{s.primary_unit.name}"

    def __new__(cls, primary_unit: Unit | ForwardRef("Quantity"), ratio: Decimal = Decimal(1), name=None):
        from .quantity import Quantity
        if type(primary_unit) is Quantity:
            ratio = primary_unit.value
            primary_unit = primary_unit.unit

        if type(primary_unit) is DerivedUnit:
            ratio *= primary_unit.ratio
            primary_unit = primary_unit.primary_unit

        if d := UnitSystem.instance().__derived_units__.get((id(primary_unit), ratio)):
            return d

        n = super().__new__(cls)
        object.__setattr__(n, 'primary_unit', primary_unit)
        object.__setattr__(n, 'ratio', ratio)
        if name:
            object.__setattr__(n, 'name', name)
        UnitSystem.instance().__derived_units__[(primary_unit, ratio)] = n
        return n

    def __pow__(self, power, modulo=None):
        return ComplexUnit(components=((self, 2),))

    def __mul__(self, other):
        if isinstance(other, (PrimaryUnit, DerivedUnit, ComplexUnit)):
            components = defaultdict(int, other._to_components())
        else:
            raise ValueError(f"cannot multiply {self} and {other}")

        components[self] += 1
        return ComplexUnit(components=tuple(components.items()))

    def __truediv__(self, other):
        if isinstance(other, (PrimaryUnit, DerivedUnit, ComplexUnit)):
            components = defaultdict(int, other._to_components(-1))
        else:
            raise ValueError(f"cannot divide {self} and {other}")

        components[self] += 1
        return ComplexUnit(components=tuple(components.items()))

    def _do_convert(self, value: NUMBER, to: 'Unit') -> NUMBER:
        primary_value = value * type(value)(self.ratio)
        if to is not self.primary_unit:
            return self.primary_unit._do_convert(primary_value, to)
        else:
            return primary_value


@dataclass(frozen=True, kw_only=True, init=False)
class OffsetDerivedUnit(DerivedUnit):
    offset: Decimal
    diff: Unit = field(default=lambda s: DiffDerivedUnit(offset_unit=s), hash=False)

    _is_multiplicative: ClassVar[bool] = False

    def __new__(cls, primary_unit: Unit | ForwardRef("Quantity"), ratio: Decimal = Decimal(1), offset: Decimal = Decimal(0), name=None):
        if type(primary_unit) is DerivedUnit:
            primary_unit = primary_unit.primary_unit
            ratio *= primary_unit.ratio

        if d := UnitSystem.instance().__derived_units__.get((primary_unit, ratio, offset)):
            return d

        n = Unit.__new__(cls)
        object.__setattr__(n, 'primary_unit', primary_unit)
        object.__setattr__(n, 'ratio', ratio)
        object.__setattr__(n, 'offset', offset)
        if name:
            object.__setattr__(n, 'name', name)
        UnitSystem.instance().__derived_units__[(id(primary_unit), ratio, offset)] = n
        return n

    def _do_convert(self, value: NUMBER, to: 'Unit') -> NUMBER:
        primary_value = (value + type(value)(self.offset)) * type(value)(self.ratio)
        if to is not self.primary_unit:
            return self.primary_unit._do_convert(primary_value, to)
        else:
            return primary_value


@dataclass(frozen=True, kw_only=True, init=False)
class DiffDerivedUnit(DerivedUnit):
    offset_unit: Unit = None
    primary_unit: Unit = lambda s: s.offset_unit.primary_unit
    ratio: Decimal = lambda s: s.offset_unit.ratio
    name: str = lambda s: f"{s.offset_unit.name}_diff"

    _is_multiplicative: ClassVar[bool] = True

    def __new__(cls, offset_unit: OffsetDerivedUnit, name=None):
        if type(offset_unit) is not OffsetDerivedUnit:
            raise ValueError(f"cannot create a diff unit from {offset_unit}")

        if d := UnitSystem.instance().__derived_units__.get((id(offset_unit), 'diff')):
            return d

        n = Unit.__new__(cls)
        object.__setattr__(n, 'offset_unit', offset_unit)
        if name:
            object.__setattr__(n, 'name', name)
        UnitSystem.instance().__derived_units__[(id(offset_unit), 'diff')] = n
        return n


@dataclass(frozen=True, kw_only=True, init=False)
class ComplexUnit(Unit):
    components: Tuple[Tuple[Unit, int], ...]
    scale: Decimal = Decimal(1)
    dimension: Dimension = lambda s: reduce(operator.mul, (u.dimension**m for u, m in s.components))
    ratio: Decimal = lambda s: reduce(operator.mul, (pow(u.ratio, m) for u, m in s.components)) * s.scale
    name: str = lambda s: s._build_name()

    def __new__(cls, components, name=None):
        from hg_oap.units.quantity import Quantity
        if isinstance(components, Quantity):
            scale = components.value
            components = components.unit._to_components()
        else:
            scale = None

        lookup_key = tuple((id(u), p) for u, p in components) + ((scale,) if scale is not None else ())
        if d := UnitSystem.instance().__complex_units__.get(lookup_key):
            return d

        assert all(u._is_multiplicative for u, _ in components)

        n = super().__new__(cls)
        object.__setattr__(n, 'components', components)

        if scale is not None:
            object.__setattr__(n, 'scale', scale)

        if name:
            object.__setattr__(n, 'name', name)

        UnitSystem.instance().__complex_units__[lookup_key] = n
        return n

    def _build_name(self):
        scale = f"{self.scale}*" if self.scale != 1 else ''
        up = '*'.join(f"{d}**{p}" if p != 1 else str(d) for d, p in self.components if p > 0) or '1'
        dn = ('*'.join(f"{d}**{abs(p)}" if p != -1 else str(d) for d, p in self.components if p < 0))
        dn = ('/' + dn) if dn else ''
        return scale + up + dn

    def _to_components(self, power=1):
        if type(self).name.__overriden__(self) or self.scale != Decimal(1):
            return super()._to_components(power)
        else:
            return self.components if power == 1 else tuple((u, p * power) for u, p in self.components)

    def __pow__(self, power, modulo=None):
        return ComplexUnit(components=tuple((u, p * power) for u, p in self.components))

    def __mul__(self, other):
        if isinstance(other, (PrimaryUnit, DerivedUnit, ComplexUnit)):
            components = defaultdict(int, other._to_components())
        else:
            raise ValueError(f"cannot multiply {self} and {other}")

        for u, p in self._to_components():
            components[u] += p

        return ComplexUnit(components=tuple(components.items()))

    def __truediv__(self, other):
        if isinstance(other, (PrimaryUnit, DerivedUnit, ComplexUnit)):
            components = defaultdict(int, other._to_components(-1))
        else:
            raise ValueError(f"cannot divide {self} and {other}")

        for u, p in self._to_components():
            components[u] += p

        return ComplexUnit(components=tuple(components.items()))

    def _do_convert(self, value: NUMBER, to: 'ComplexUnit') -> NUMBER:
        return type(value)(self.ratio / to.ratio) * value
