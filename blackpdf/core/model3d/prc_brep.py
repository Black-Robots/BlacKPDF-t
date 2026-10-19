"""Geometría exacta de PRC (B-rep y alambres) y su teselación.

Asymptote (LaTeX, media9) guarda las superficies como geometría exacta sin
teselar: parches de Bézier en B-rep comprimido, superficies NURBS y
primitivas (esfera, cilindro, cono, toro, disco y tubo), y las curvas como
NURBS o polilíneas. Este módulo lee las secciones de geometría del PRC y las
convierte en mallas de triángulos y líneas.

Solo se admiten caras sin recortar, que es lo que escriben estos programas;
los exportadores CAD guardan además una teselación, que es la que se usa.
Ante algo que no se entiende se lanza PRCError y el lector sigue con lo que
ya tenga.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from .prc import (
    ASM, CRV, ROOT, SURF, TESS_3D_COMPRESSED, TOPO, BitReader, PRCError, _base_geometry, _cartesian_matrix,
    _content_base, _expect, _graphics, _user_data,
)
from .scene import Matrix, transform_point

CRV_NURBS = CRV + 3
CRV_CIRCLE = CRV + 4
CRV_COMPOSITE = CRV + 5
CRV_POLYLINE = CRV + 15

SURF_BLEND01 = SURF + 2
SURF_NURBS = SURF + 5
SURF_CONE = SURF + 6
SURF_CYLINDER = SURF + 7
SURF_RULED = SURF + 12
SURF_SPHERE = SURF + 13
SURF_TORUS = SURF + 17

TOPO_CONTEXT = TOPO + 1
TOPO_WIRE_EDGE = TOPO + 5
TOPO_FACE = TOPO + 9
TOPO_SHELL = TOPO + 10
TOPO_CONNEX = TOPO + 11
TOPO_SINGLE_WIRE_BODY = TOPO + 13
TOPO_BREP_DATA = TOPO + 14
TOPO_SINGLE_WIRE_BODY_COMPRESS = TOPO + 15
TOPO_BREP_DATA_COMPRESS = TOPO + 16

ASM_GEOMETRY = ASM + 6
ASM_EXTRA_GEOMETRY = ASM + 7

HCG_ANA_NURBS = 13
"""Tipo de cara comprimida «NURBS» (la única que escribe Asymptote)."""

MAX_SEGMENTS = 16
"""Divisiones máximas de un parche por dirección."""

ANGLE_STEP = math.pi / 24
"""Paso angular de esferas, cilindros, conos y toros (7,5 grados)."""


# -- curvas ---------------------------------------------------------------------------


def _lerp(a, b, t):
    return tuple(x + (y - x) * t for x, y in zip(a, b))


def _span(knots: list[float], degree: int, count: int, t: float) -> int:
    """Índice k con knots[k] <= t < knots[k + 1] dentro del dominio."""
    low, high = degree, count - 1
    if t >= knots[high + 1]:
        return high
    if t <= knots[low]:
        return low
    while low < high:
        middle = (low + high + 1) // 2
        if knots[middle] <= t:
            low = middle
        else:
            high = middle - 1
    return low


def _de_boor(degree: int, knots: list[float], points: list, t: float):
    """Punto de una B-spline (coordenadas homogéneas) por el algoritmo de De Boor."""
    k = _span(knots, degree, len(points), t)
    d = [points[j + k - degree] for j in range(degree + 1)]
    for r in range(1, degree + 1):
        for j in range(degree, r - 1, -1):
            low = knots[j + k - degree]
            high = knots[j + 1 + k - r]
            alpha = (t - low) / (high - low) if high != low else 0.0
            d[j] = _lerp(d[j - 1], d[j], alpha)
    return d[degree]


def _project(h) -> tuple[float, float, float]:
    w = h[3]
    if w == 0.0 or w == 1.0:
        return (h[0], h[1], h[2])
    return (h[0] / w, h[1] / w, h[2] / w)


def _knot_samples(knots: list[float], degree: int, count: int, per_span: int, limit: int = 64) -> list[float]:
    """Parámetros de muestreo: cada tramo entre nudos dividido en partes iguales."""
    start, end = knots[degree], knots[count]
    breaks = sorted({k for k in knots[degree:count + 1] if start <= k <= end})
    if len(breaks) < 2:
        return [start, end]
    spans = len(breaks) - 1
    per_span = max(1, min(per_span, limit // spans))
    samples = []
    for a, b in zip(breaks, breaks[1:]):
        for step in range(per_span):
            samples.append(a + (b - a) * step / per_span)
    samples.append(end)
    return samples


class Curve:
    """Curva paramétrica con su transformación ya aplicada."""

    domain: tuple[float, float] = (0.0, 1.0)

    def point(self, t: float) -> tuple[float, float, float]:
        raise NotImplementedError

    def samples(self) -> list[float]:
        a, b = self.domain
        return [a + (b - a) * i / 32 for i in range(33)]

    def polyline(self) -> list[tuple[float, float, float]]:
        return [self.point(t) for t in self.samples()]


class NurbsCurve(Curve):
    def __init__(self, degree: int, points: list, knots: list[float]):
        if degree < 1 or len(knots) != len(points) + degree + 1:
            raise PRCError("curva NURBS no válida")
        self.degree = degree
        self.points = points
        self.knots = knots
        self.domain = (knots[degree], knots[len(points)])

    def point(self, t: float):
        return _project(_de_boor(self.degree, self.knots, self.points, t))

    def samples(self) -> list[float]:
        per_span = 1 if self.degree == 1 else 16
        return _knot_samples(self.knots, self.degree, len(self.points), per_span, 512)


class _Mapped(Curve):
    """Curva en coordenadas locales con parametrización y transformación."""

    def __init__(self, matrix: Matrix | None, interval: tuple[float, float], a: float, b: float):
        self.matrix = matrix
        self.domain = interval
        self.a = a
        self.b = b

    def local(self, s: float):
        raise NotImplementedError

    def point(self, t: float):
        p = self.local(self.a * t + self.b)
        return transform_point(self.matrix, p) if self.matrix is not None else p


class PolyLine(_Mapped):
    def __init__(self, matrix, interval, a, b, points):
        super().__init__(matrix, interval, a, b)
        if not points:
            raise PRCError("polilínea vacía")
        self.points = points

    def local(self, s: float):
        last = len(self.points) - 1
        if last == 0:
            return self.points[0]
        s = min(max(s, 0.0), float(last))
        index = min(int(s), last - 1)
        return _lerp(self.points[index], self.points[index + 1], s - index)

    def samples(self) -> list[float]:
        # Un punto por vértice, expresado en el parámetro de la curva.
        a, b = self.domain
        last = len(self.points) - 1
        if self.a == 0 or last == 0:
            return [a, b]
        values = [(i - self.b) / self.a for i in range(last + 1)]
        return [v for v in values if min(a, b) - 1e-9 <= v <= max(a, b) + 1e-9] or [a, b]


class Circle(_Mapped):
    def __init__(self, matrix, interval, a, b, radius: float):
        super().__init__(matrix, interval, a, b)
        self.radius = radius

    def local(self, s: float):
        return (self.radius * math.cos(s), self.radius * math.sin(s), 0.0)

    def samples(self) -> list[float]:
        a, b = self.domain
        count = max(4, min(128, math.ceil(abs((b - a) * self.a) / ANGLE_STEP)))
        return [a + (b - a) * i / count for i in range(count + 1)]


class Composite(Curve):
    def __init__(self, matrix, curves: list[tuple[Curve, bool]]):
        self.matrix = matrix
        self.curves = curves

    def point(self, t: float):
        raise PRCError("curva compuesta sin parámetro")

    def polyline(self):
        points = []
        for curve, sense in self.curves:
            part = curve.polyline()
            if not sense:
                part.reverse()
            points.extend(part)
        if self.matrix is not None:
            points = [transform_point(self.matrix, p) for p in points]
        return points


# -- superficies -----------------------------------------------------------------------


class Surface:
    """Superficie paramétrica: dominio, puntos y parámetros de muestreo."""

    def domain(self) -> tuple[tuple[float, float], tuple[float, float]]:
        raise NotImplementedError

    def point(self, u: float, v: float) -> tuple[float, float, float]:
        raise NotImplementedError

    def grid(self, domain) -> tuple[list[float], list[float]]:
        (u0, u1), (v0, v1) = domain
        return _uniform(u0, u1, 8), _uniform(v0, v1, 8)


def _uniform(a: float, b: float, count: int) -> list[float]:
    count = max(1, count)
    return [a + (b - a) * i / count for i in range(count + 1)]


class NurbsSurface(Surface):
    """NURBS o Bézier; ``points[i][j]`` en coordenadas homogéneas, i en u y j en v."""

    def __init__(self, degree_u: int, degree_v: int, points: list[list], knots_u: list[float], knots_v: list[float]):
        nu, nv = len(points), len(points[0]) if points else 0
        if (degree_u < 1 or degree_v < 1 or nu <= degree_u or nv <= degree_v
                or len(knots_u) != nu + degree_u + 1 or len(knots_v) != nv + degree_v + 1):
            raise PRCError("superficie NURBS no válida")
        self.degree_u = degree_u
        self.degree_v = degree_v
        self.points = points
        self.knots_u = knots_u
        self.knots_v = knots_v
        self.bezier = nu == degree_u + 1 and nv == degree_v + 1 and all(len(row) == 4 and row[3] == 1.0 for line in points for row in line)

    def domain(self):
        return ((self.knots_u[self.degree_u], self.knots_u[len(self.points)]),
                (self.knots_v[self.degree_v], self.knots_v[len(self.points[0])]))

    def point(self, u: float, v: float):
        rows = [_de_boor(self.degree_v, self.knots_v, line, v) for line in self.points]
        return _project(_de_boor(self.degree_u, self.knots_u, rows, u))

    def grid(self, domain, segments: int | None = None):
        (u0, u1), (v0, v1) = domain
        full = self.domain()
        if self.bezier or (u0, u1) != full[0] or (v0, v1) != full[1]:
            n = segments or (_bezier_segments(self) if self.bezier else 8)
            nu = 1 if self.degree_u == 1 and n == 1 else n
            nv = 1 if self.degree_v == 1 and n == 1 else n
            return _uniform(u0, u1, nu), _uniform(v0, v1, nv)
        us = _knot_samples(self.knots_u, self.degree_u, len(self.points), 1 if self.degree_u == 1 else 6)
        vs = _knot_samples(self.knots_v, self.degree_v, len(self.points[0]), 1 if self.degree_v == 1 else 6)
        return us, vs


def _bezier_segments(surface: NurbsSurface) -> int:
    """Divisiones según lo que se aparta el parche del cuadrilátero de sus esquinas."""
    p = surface.points
    corners = [p[0][0][:3], p[-1][0][:3], p[0][-1][:3], p[-1][-1][:3]]
    xs = [c[0] for line in p for c in line]
    ys = [c[1] for line in p for c in line]
    zs = [c[2] for line in p for c in line]
    size = math.dist((min(xs), min(ys), min(zs)), (max(xs), max(ys), max(zs)))
    if size == 0.0:
        return 1
    (u0, u1), (v0, v1) = surface.domain()
    deviation = 0.0
    for a in (0.25, 0.5, 0.75):
        for b in (0.25, 0.5, 0.75):
            point = surface.point(u0 + (u1 - u0) * a, v0 + (v1 - v0) * b)
            flat = _lerp(_lerp(corners[0], corners[1], a), _lerp(corners[2], corners[3], a), b)
            deviation = max(deviation, math.dist(point, flat))
    ratio = deviation / size
    if ratio < 1e-6:
        return 1
    return max(2, min(MAX_SEGMENTS, math.ceil(math.sqrt(ratio / 0.0015))))


@dataclass
class UVParameterization:
    swap: bool
    domain: tuple[tuple[float, float], tuple[float, float]]
    a_u: float
    a_v: float
    b_u: float
    b_v: float


class Analytic(Surface):
    """Esfera, cilindro, cono, toro, superficie reglada o tubo (Blend01)."""

    def __init__(self, kind: int, matrix: Matrix | None, uv: UVParameterization, **values):
        self.kind = kind
        self.matrix = matrix
        self.uv = uv
        self.values = values

    def domain(self):
        (u0, v0), (u1, v1) = self.uv.domain
        return (u0, u1), (v0, v1)

    def point(self, u: float, v: float):
        s = self.uv.a_u * u + self.uv.b_u
        w = self.uv.a_v * v + self.uv.b_v
        if self.uv.swap:
            s, w = w, s
        p = self._local(s, w)
        return transform_point(self.matrix, p) if self.matrix is not None else p

    def _local(self, s: float, w: float):
        values = self.values
        kind = self.kind
        if kind == SURF_SPHERE:
            r = values["radius"]
            return (r * math.cos(w) * math.cos(s), r * math.cos(w) * math.sin(s), r * math.sin(w))
        if kind == SURF_CYLINDER:
            r = values["radius"]
            return (r * math.cos(s), r * math.sin(s), w)
        if kind == SURF_CONE:
            r = values["radius"] + w * math.tan(values["semi_angle"])
            return (r * math.cos(s), r * math.sin(s), w)
        if kind == SURF_TORUS:
            ring = values["major"] + values["minor"] * math.cos(w)
            return (ring * math.cos(s), ring * math.sin(s), values["minor"] * math.sin(w))
        if kind == SURF_RULED:
            return _lerp(values["first"].point(w), values["second"].point(w), s)
        if kind == SURF_BLEND01:
            center = values["center"]
            c = center.point(w)
            radius = [o - x for o, x in zip(values["origin"].point(w), c)]
            a, b = center.domain
            h = (b - a) * 1e-4 or 1e-6
            ahead = center.point(min(max(w + h, min(a, b)), max(a, b)))
            behind = center.point(min(max(w - h, min(a, b)), max(a, b)))
            tangent = [x - y for x, y in zip(ahead, behind)]
            normal = _cross(tangent, radius)
            length = math.sqrt(sum(x * x for x in normal))
            size = math.sqrt(sum(x * x for x in radius))
            if length > 0.0:
                normal = [x * size / length for x in normal]
            return tuple(c[k] + math.cos(s) * radius[k] + math.sin(s) * normal[k] for k in range(3))
        raise PRCError(f"superficie no admitida {kind}")

    def grid(self, domain):
        (u0, u1), (v0, v1) = domain

        def angular(a, b, coefficient):
            return _uniform(a, b, max(2, min(96, math.ceil(abs((b - a) * coefficient) / ANGLE_STEP))))

        uv = self.uv
        su = (lambda a, b: angular(a, b, uv.a_u))
        sv = (lambda a, b: angular(a, b, uv.a_v))
        kind = self.kind
        if kind in (SURF_CYLINDER, SURF_CONE):
            # u (o v si están cambiados) es el ángulo; el otro parámetro es lineal.
            if uv.swap:
                return _uniform(u0, u1, 1), sv(v0, v1)
            return su(u0, u1), _uniform(v0, v1, 1)
        if kind in (SURF_SPHERE, SURF_TORUS):
            return su(u0, u1), sv(v0, v1)
        if kind == SURF_RULED:
            curve = self.values["first"]
            count = len(curve.samples()) - 1 if not isinstance(curve, Composite) else 32
            return _uniform(u0, u1, 1), _uniform(v0, v1, max(1, min(128, count)))
        if kind == SURF_BLEND01:
            center = self.values["center"]
            count = len(center.samples()) - 1
            return _uniform(u0, u1, 24), _uniform(v0, v1, max(1, min(256, count)))
        return super().grid(domain)


def _cross(a, b):
    return [a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]]


# -- topología ------------------------------------------------------------------------


@dataclass
class Face:
    surface: Surface | None
    trim: tuple[tuple[float, float], tuple[float, float]] | None = None
    style: int = -1


@dataclass
class Body:
    kind: str  # "brep" o "wire"
    faces: list[Face] = field(default_factory=list)
    curves: list[Curve] = field(default_factory=list)


class _Context:
    """Elementos ya leídos de un contexto, para las referencias «ya guardado»."""

    def __init__(self):
        self.curves: list = []
        self.surfaces: list = []
        self.topology: list = []


def _pointer(r: BitReader, stored: list, read):
    if r.boolean():
        index = r.uint()
        if index >= len(stored):
            raise PRCError("referencia a geometría inexistente")
        return stored[index]
    kind = r.uint()
    if kind == ROOT:
        return None
    item = read(r, kind)
    stored.append(item)
    return item


def _transformation(r: BitReader, is_3d: bool = True) -> Matrix | None:
    if not r.bit():
        return None
    behaviour = r.char()
    if behaviour & ~0x0F:
        raise PRCError("transformación de geometría no admitida")
    vector = r.vector3 if is_3d else (lambda: (*r.vector2(), 0.0))
    origin = vector() if behaviour & 0x01 else (0.0, 0.0, 0.0)
    x_axis, y_axis = (1.0, 0.0, 0.0), (0.0, 1.0, 0.0)
    if behaviour & 0x02:
        x_axis, y_axis = vector(), vector()
    scale = r.double() if behaviour & 0x08 else None
    return _cartesian_matrix(behaviour, origin, x_axis, y_axis, None, scale, None)


def _parameterization(r: BitReader) -> tuple[tuple[float, float], float, float]:
    interval = (r.double(), r.double())
    return interval, r.double(), r.double()


def _uv_parameterization(r: BitReader) -> UVParameterization:
    swap = r.boolean()
    domain = (r.vector2(), r.vector2())
    a_u, a_v, b_u, b_v = r.double(), r.double(), r.double(), r.double()
    return UVParameterization(swap, domain, a_u, a_v, b_u, b_v)


def _curve(r: BitReader, kind: int, ctx: _Context) -> Curve:
    _base_geometry(r)
    r.uint()  # extend_info
    is_3d = r.boolean()
    if kind == CRV_NURBS:
        rational = r.boolean()
        degree = r.uint()
        count = r.count() + 1
        knot_count = r.count() + 1
        points = []
        for _ in range(count):
            x, y = r.double(), r.double()
            z = r.double() if is_3d else 0.0
            w = r.double() if rational else 1.0
            points.append((x, y, z, w))
        knots = [r.double() for _ in range(knot_count)]
        r.uint()  # knot_type
        r.uint()  # curve_form
        return NurbsCurve(degree, points, knots)
    matrix = _transformation(r, is_3d)
    interval, a, b = _parameterization(r)
    if kind == CRV_POLYLINE:
        count = r.count()
        points = [r.vector3() if is_3d else (*r.vector2(), 0.0) for _ in range(count)]
        return PolyLine(matrix, interval, a, b, points)
    if kind == CRV_CIRCLE:
        return Circle(matrix, interval, a, b, r.double())
    if kind == CRV_COMPOSITE:
        curves = []
        for _ in range(r.count()):
            curve = _pointer(r, ctx.curves, lambda rr, k: _curve(rr, k, ctx))
            sense = r.boolean()
            if curve is not None:
                curves.append((curve, sense))
        r.boolean()  # cerrada
        return Composite(matrix, curves)
    raise PRCError(f"curva no admitida {kind}")


def _surface(r: BitReader, kind: int, ctx: _Context) -> Surface:
    _base_geometry(r)
    r.uint()  # extend_info
    if kind == SURF_NURBS:
        rational = r.boolean()
        degree_u, degree_v = r.uint(), r.uint()
        nu, nv = r.count() + 1, r.count() + 1
        knot_u, knot_v = r.count() + 1, r.count() + 1
        if nu * nv > 4_000_000:
            raise PRCError("superficie NURBS demasiado grande")
        points = []
        for _ in range(nu):
            line = []
            for _ in range(nv):
                x, y, z = r.double(), r.double(), r.double()
                line.append((x, y, z, r.double() if rational else 1.0))
            points.append(line)
        knots_u = [r.double() for _ in range(knot_u)]
        knots_v = [r.double() for _ in range(knot_v)]
        r.uint()  # knot_type
        r.uint()  # surface_form
        return NurbsSurface(degree_u, degree_v, points, knots_u, knots_v)
    matrix = _transformation(r)
    uv = _uv_parameterization(r)
    if kind == SURF_SPHERE:
        return Analytic(kind, matrix, uv, radius=r.double())
    if kind == SURF_CYLINDER:
        return Analytic(kind, matrix, uv, radius=r.double())
    if kind == SURF_CONE:
        radius = r.double()
        return Analytic(kind, matrix, uv, radius=radius, semi_angle=r.double())
    if kind == SURF_TORUS:
        major = r.double()
        return Analytic(kind, matrix, uv, major=major, minor=r.double())
    if kind == SURF_RULED:
        first = _pointer(r, ctx.curves, lambda rr, k: _curve(rr, k, ctx))
        second = _pointer(r, ctx.curves, lambda rr, k: _curve(rr, k, ctx))
        if first is None or second is None:
            raise PRCError("superficie reglada incompleta")
        return Analytic(kind, matrix, uv, first=first, second=second)
    if kind == SURF_BLEND01:
        center = _pointer(r, ctx.curves, lambda rr, k: _curve(rr, k, ctx))
        origin = _pointer(r, ctx.curves, lambda rr, k: _curve(rr, k, ctx))
        _pointer(r, ctx.curves, lambda rr, k: _curve(rr, k, ctx))  # tangente
        if center is None or origin is None:
            raise PRCError("tubo incompleto")
        return Analytic(kind, matrix, uv, center=center, origin=origin)
    raise PRCError(f"superficie no admitida {kind}")


def _topology(r: BitReader, kind: int, ctx: _Context):
    if kind == TOPO_FACE:
        _base_geometry(r)
        surface = _pointer(r, ctx.surfaces, lambda rr, k: _surface(rr, k, ctx))
        trim = None
        if r.bit():
            (u0, v0), (u1, v1) = r.vector2(), r.vector2()
            trim = ((u0, u1), (v0, v1))
        if r.bit():
            r.double()  # tolerancia
        loops = r.uint()
        r.int()  # índice del contorno exterior
        if loops:
            raise PRCError("caras recortadas")
        return [Face(surface, trim)]
    if kind == TOPO_SHELL:
        _base_geometry(r)
        r.boolean()  # cerrada
        faces = []
        for _ in range(r.count()):
            faces.extend(_pointer(r, ctx.topology, lambda rr, k: _topology(rr, k, ctx)) or [])
            r.char()  # orientación
        return faces
    if kind == TOPO_CONNEX:
        _base_geometry(r)
        faces = []
        for _ in range(r.count()):
            faces.extend(_pointer(r, ctx.topology, lambda rr, k: _topology(rr, k, ctx)) or [])
        return faces
    if kind == TOPO_WIRE_EDGE:
        _base_geometry(r)
        curve = _pointer(r, ctx.curves, lambda rr, k: _curve(rr, k, ctx))
        if r.bit():
            r.double()
            r.double()
        return curve
    raise PRCError(f"topología no admitida {kind}")


# -- B-rep comprimido (parches de Bézier de Asymptote) ---------------------------------------


def _varint(r: BitReader, bits: int) -> int:
    negative = r.bit()
    value = r.bits(bits - 1)
    return -value if negative else value


def _bits_then_uint(r: BitReader) -> int:
    return r.bits(r.bits(5))


def _multiplicities(r: BitReader, degree: int) -> list[int]:
    # Ancho: log2 de degree + 2 redondeado hacia arriba, como el escritor.
    width = (degree + 1).bit_length() if degree else 2
    result = []
    previous = None
    for _ in range(r.bits(16)):
        if r.boolean():
            if previous is None:
                raise PRCError("multiplicidad sin valor previo")
            result.append(previous)
        else:
            previous = r.bits(width)
            result.append(previous)
    return result


def _compressed_nurbs(r: BitReader, tolerance: float) -> NurbsSurface:
    degree_u, degree_v = r.bits(5), r.bits(5)
    mult_u = _multiplicities(r, degree_u)
    mult_v = _multiplicities(r, degree_v)
    r.boolean()  # cerrada en u
    r.boolean()  # cerrada en v
    nu = sum(mult_u) - degree_u - 1
    nv = sum(mult_v) - degree_v - 1
    if not (degree_u >= 1 and degree_v >= 1 and nu > degree_u and nv > degree_v and nu * nv <= 1_000_000):
        raise PRCError("parche comprimido no válido")
    iso_bits = r.bits(20) + 1
    rest_bits = r.bits(20) + 1
    if iso_bits > 33 or rest_bits > 33:
        raise PRCError("parche comprimido no válido")
    step = 0.2 * tolerance

    def scaled(bits: int):
        return (_varint(r, bits) * step, _varint(r, bits) * step, _varint(r, bits) * step)

    P = [[None] * nv for _ in range(nu)]
    P[0][0] = r.vector3()
    for j in range(1, nv):
        d = scaled(iso_bits)
        P[0][j] = tuple(a + b for a, b in zip(P[0][j - 1], d))
    for i in range(1, nu):
        d = scaled(iso_bits)
        P[i][0] = tuple(a + b for a, b in zip(P[i - 1][0], d))
    for i in range(1, nu):
        for j in range(1, nv):
            kind = r.bits(2)
            corner = P[i - 1][j - 1]
            U = [a - b for a, b in zip(P[i][j - 1], corner)]
            V = [a - b for a, b in zip(P[i - 1][j], corner)]
            base = [corner[k] + U[k] + V[k] for k in range(3)]
            if kind == 0:
                P[i][j] = tuple(base)
            elif kind == 3:
                d = scaled(rest_bits)
                P[i][j] = tuple(base[k] + d[k] for k in range(3))
            else:
                ue = _unit(U)
                ne = _unit(_cross(U, V))
                if ue is None or ne is None:
                    raise PRCError("parche comprimido degenerado")
                nue = _cross(ne, ue)
                if kind == 1:
                    z = _varint(r, rest_bits) * step
                    P[i][j] = tuple(base[k] + z * ne[k] for k in range(3))
                else:
                    x = _varint(r, rest_bits) * step
                    y = _varint(r, rest_bits) * step
                    P[i][j] = tuple(base[k] + x * ue[k] + y * nue[k] for k in range(3))
    uniform_u, uniform_v, rational = r.boolean(), r.boolean(), r.boolean()
    if not uniform_u or not uniform_v or rational:
        raise PRCError("parche comprimido no admitido")
    knots_u = [float(index) for index, count in enumerate(mult_u) for _ in range(count)]
    knots_v = [float(index) for index, count in enumerate(mult_v) for _ in range(count)]
    points = [[(*p, 1.0) for p in line] for line in P]
    return NurbsSurface(degree_u, degree_v, points, knots_u, knots_v)


def _unit(v):
    length = math.sqrt(v[0] * v[0] + v[1] * v[1] + v[2] * v[2])
    if length < 1.1920929e-07:  # FLT_EPSILON, como el escritor
        return None
    return [x / length for x in v]


def _compressed_brep(r: BitReader) -> list[Face]:
    tolerance = r.double()
    reference_bits = _bits_then_uint(r)
    if r.bits(reference_bits) or r.bits(reference_bits):
        raise PRCError("B-rep comprimido con vértices o aristas")
    if not r.boolean():
        raise PRCError("B-rep comprimido con varias cáscaras")
    count = 1 if r.boolean() else _bits_then_uint(r)
    faces = []
    for _ in range(count):
        if r.bit():
            raise PRCError("curva comprimida no admitida")
        kind = r.bits(4)
        if kind != HCG_ANA_NURBS:
            raise PRCError(f"cara comprimida no admitida {kind}")
        r.boolean()  # orientación
        if r.boolean():
            raise PRCError("cara comprimida recortada")
        faces.append(Face(_compressed_nurbs(r, tolerance)))
    for _ in range(count):
        if r.boolean():
            raise PRCError("cara iso no admitida")
    for _ in range(count):
        _base_geometry(r)
    return faces


# -- secciones ------------------------------------------------------------------------


def _body(r: BitReader, ctx: _Context) -> Body:
    kind = r.uint()
    _base_geometry(r)  # contenido del cuerpo
    behaviour = r.char()
    if kind == TOPO_BREP_DATA:
        faces = []
        for _ in range(r.count()):
            faces.extend(_pointer(r, ctx.topology, lambda rr, k: _topology(rr, k, ctx)) or [])
        if behaviour:
            r.vector3()
            r.vector3()
        return Body("brep", faces=faces)
    if kind == TOPO_BREP_DATA_COMPRESS:
        return Body("brep", faces=_compressed_brep(r))
    if kind == TOPO_SINGLE_WIRE_BODY:
        curve = _pointer(r, ctx.topology, lambda rr, k: _topology(rr, k, ctx))
        return Body("wire", curves=[curve] if isinstance(curve, Curve) else [])
    raise PRCError(f"cuerpo no admitido {kind}")


def read_geometry(r: BitReader, warnings: list[str] | None = None) -> list[list[Body | None]]:
    """Sección de geometría exacta: contextos con sus cuerpos.

    Un cuerpo que no se puede leer detiene la lectura (el formato no permite
    saltarlo), pero se conserva lo leído hasta ahí y el motivo va a
    ``warnings``. Sin ``warnings`` el error se propaga.
    """
    _expect(r, ASM_GEOMETRY)
    _content_base(r, ASM_GEOMETRY)
    contexts: list[list[Body | None]] = []
    try:
        for _ in range(r.count()):
            _expect(r, TOPO_CONTEXT)
            _content_base(r, TOPO_CONTEXT)
            r.char()
            r.double()  # granularidad
            r.double()  # tolerancia
            if r.boolean():
                r.double()
            if r.boolean():
                r.double()
            bodies: list[Body | None] = []
            contexts.append(bodies)
            ctx = _Context()
            for _ in range(r.count()):
                bodies.append(_body(r, ctx))
        _user_data(r)
    except (PRCError, IndexError, ValueError, OverflowError, ZeroDivisionError) as error:
        if warnings is None:
            raise
        warnings.append(f"prc-geometry: {error}")
    return contexts


def apply_face_graphics(r: BitReader, contexts: list[list[Body | None]]) -> None:
    """Sección de geometría extra: estilos por cara (caras de varios colores)."""
    _expect(r, ASM_EXTRA_GEOMETRY)
    _content_base(r, ASM_EXTRA_GEOMETRY)
    for index in range(r.count()):
        for _ in range(r.count()):
            kind = r.uint()
            if kind in (TOPO_BREP_DATA_COMPRESS, TOPO_SINGLE_WIRE_BODY_COMPRESS, TESS_3D_COMPRESSED):
                r.double()
        r.current_graphics = (-1, -1, 1)
        faces = []
        if index < len(contexts):
            faces = [face for body in contexts[index] if body is not None for face in body.faces]
        for _ in range(r.count()):
            element = r.uint()
            for number in range(r.count()):
                if r.boolean():
                    graphics = _graphics(r)
                    if element == TOPO_FACE and number < len(faces) and graphics.style >= 0:
                        faces[number].style = graphics.style


# -- teselación ------------------------------------------------------------------------


class Mesh:
    """Triángulos con vértices soldados por posición."""

    def __init__(self):
        self.positions: list[tuple[float, float, float]] = []
        self.triangles: list[tuple[int, int, int]] = []
        self._index: dict[tuple, int] = {}

    def vertex(self, p) -> int:
        key = (round(p[0], 9), round(p[1], 9), round(p[2], 9))
        index = self._index.get(key)
        if index is None:
            index = self._index[key] = len(self.positions)
            self.positions.append((float(p[0]), float(p[1]), float(p[2])))
        return index

    def add_faces(self, faces: list[Face]) -> None:
        """Añade caras; los parches de Bézier comparten subdivisión para que
        los bordes comunes coincidan (sin grietas por uniones en T)."""
        patches = [f.surface for f in faces if isinstance(f.surface, NurbsSurface) and f.surface.bezier]
        segments = max((_bezier_segments(patch) for patch in patches), default=None)
        for face in faces:
            self.add_face(face, segments)

    def add_face(self, face: Face, segments: int | None = None) -> None:
        surface = face.surface
        if surface is None:
            return
        domain = face.trim or surface.domain()
        if isinstance(surface, NurbsSurface):
            us, vs = surface.grid(domain, segments)
        else:
            us, vs = surface.grid(domain)
        if len(us) * len(vs) > 200_000:
            raise PRCError("superficie demasiado grande")
        grid = []
        for u in us:
            row = []
            for v in vs:
                p = surface.point(u, v)
                if not all(math.isfinite(c) for c in p):
                    raise PRCError("punto no válido en la superficie")
                row.append(self.vertex(p))
            grid.append(row)
        for i in range(len(us) - 1):
            for j in range(len(vs) - 1):
                a, b, c, d = grid[i][j], grid[i + 1][j], grid[i + 1][j + 1], grid[i][j + 1]
                for tri in ((a, b, c), (a, c, d)):
                    if tri[0] != tri[1] and tri[1] != tri[2] and tri[0] != tri[2]:
                        self.triangles.append(tri)


def curve_points(curve: Curve) -> list[tuple[float, float, float]]:
    points = curve.polyline()
    return [p for p in points if all(math.isfinite(c) for c in p)]
