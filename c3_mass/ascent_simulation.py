"""T0/T1 ascent simulation core (Phase B), pure Python.

T0 is the ideal rocket-equation estimate.  T1 is a planar 3DOF point-mass
ascent: spherical gravity, exponential atmosphere, drag, thrust/Isp, mass flow,
staging and a simple pitch program.  It does not touch KSP or the database;
callers provide synthetic or profile-derived inputs.

This is deliberately a skeleton: it is not yet a full FAR/6DOF or orbit-
insertion solver, and it does not claim flight accuracy.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
import math


G0 = 9.80665


@dataclass(frozen=True)
class Planet:
    name: str = 'Earth'
    mu: float = 3.986004418e14       # m^3/s^2
    radius: float = 6371000.0         # m
    atmosphere_height: float = 140000.0
    density0: float = 1.225           # kg/m^3 at sea level
    scale_height: float = 8500.0      # m

    def density(self, altitude_m: float) -> float:
        if altitude_m < 0 or altitude_m > self.atmosphere_height:
            return 0.0
        return self.density0 * math.exp(-altitude_m / self.scale_height)

    def gravity(self, radius_m: float) -> float:
        if self.mu <= 0 or radius_m <= 0:
            return 0.0
        return -self.mu / (radius_m * radius_m)


@dataclass(frozen=True)
class StageSpec:
    dry_mass_t: float
    propellant_t: float
    thrust_kN: float
    isp_s: float
    area_m2: float = 0.0
    drag_coefficient: float = 0.0

    def __post_init__(self):
        for name in ('dry_mass_t', 'propellant_t', 'thrust_kN', 'isp_s', 'area_m2', 'drag_coefficient'):
            value = getattr(self, name)
            if not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
                raise ValueError(f'StageSpec.{name} must be a finite non-negative number')
        if self.isp_s <= 0:
            raise ValueError('StageSpec.isp_s must be > 0')
        if self.dry_mass_t <= 0:
            raise ValueError('StageSpec.dry_mass_t must be > 0')


@dataclass(frozen=True)
class PitchProgram:
    vertical_altitude_m: float = 1000.0
    turn_rate_deg_per_km: float = 2.0
    max_pitch_deg: float = 90.0
    min_pitch_deg: float = 0.0

    def pitch_deg(self, altitude_m: float) -> float:
        if altitude_m <= self.vertical_altitude_m:
            return self.max_pitch_deg
        turned = (altitude_m - self.vertical_altitude_m) / 1000.0 * self.turn_rate_deg_per_km
        return max(self.min_pitch_deg, self.max_pitch_deg - turned)


@dataclass(frozen=True)
class AscentSettings:
    dt_s: float = 0.05
    max_time_s: float = 900.0
    launch_altitude_m: float = 0.0
    initial_tangential_m_s: float = 0.0

    def __post_init__(self):
        if self.dt_s <= 0 or self.max_time_s <= 0:
            raise ValueError('dt_s and max_time_s must be > 0')


@dataclass
class AscentSample:
    time_s: float
    altitude_m: float
    downrange_m: float
    radial_velocity_m_s: float
    tangential_velocity_m_s: float
    speed_m_s: float
    mass_t: float
    dynamic_pressure_Pa: float
    acceleration_m_s2: float
    thrust_kN: float
    stage_index: int

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class StageEvent:
    time_s: float
    stage_index: int
    event: str  # jettison
    altitude_m: float
    mass_before_t: float
    mass_after_t: float

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class AscentResult:
    stages: list[StageSpec]
    payload_t: float
    planet: Planet
    pitch: PitchProgram
    settings: AscentSettings
    samples: list[AscentSample] = field(default_factory=list)
    stage_events: list[StageEvent] = field(default_factory=list)
    final: AscentSample | None = None
    max_dynamic_pressure_Pa: float = 0.0
    max_acceleration_m_s2: float = 0.0
    initial_mass_t: float = 0.0
    final_mass_t: float = 0.0
    stopped_reason: str = ''

    def to_dict(self) -> dict:
        return {
            'stages': [asdict(stage) for stage in self.stages],
            'payload_t': self.payload_t,
            'planet': asdict(self.planet),
            'pitch': asdict(self.pitch),
            'settings': asdict(self.settings),
            'samples': [sample.to_dict() for sample in self.samples],
            'stage_events': [event.to_dict() for event in self.stage_events],
            'final': self.final.to_dict() if self.final is not None else None,
            'max_dynamic_pressure_Pa': self.max_dynamic_pressure_Pa,
            'max_acceleration_m_s2': self.max_acceleration_m_s2,
            'initial_mass_t': self.initial_mass_t,
            'final_mass_t': self.final_mass_t,
            'stopped_reason': self.stopped_reason,
        }


def _stage_wet(stage: StageSpec) -> float:
    return stage.dry_mass_t + stage.propellant_t


def total_ideal_dv(stages: list[StageSpec], payload_t: float = 0.0) -> float:
    """T0: ideal vacuum delta-v with the given payload above all stages."""
    if not stages:
        raise ValueError('at least one stage is required')
    if payload_t < 0 or not math.isfinite(payload_t):
        raise ValueError('payload_t must be finite and >= 0')
    mass_above = payload_t
    dv = 0.0
    for stage in reversed(stages):
        wet = _stage_wet(stage) + mass_above
        dry = stage.dry_mass_t + mass_above
        if dry <= 0 or wet <= dry:
            raise ValueError('stage masses are not physically valid')
        dv += stage.isp_s * G0 * math.log(wet / dry)
        mass_above += _stage_wet(stage)
    return dv


def _state_derivative(state, stage: StageSpec | None, planet: Planet, pitch_deg: float):
    radius, _, radial, tangential, mass = state
    altitude = radius - planet.radius
    speed = math.hypot(radial, tangential)
    density = planet.density(altitude)
    drag_acc = 0.0
    dynamic_pressure = 0.0
    if stage is not None and speed > 0 and density > 0 and stage.area_m2 > 0 and stage.drag_coefficient > 0:
        dynamic_pressure = 0.5 * density * speed * speed
        drag_acc = dynamic_pressure * stage.drag_coefficient * stage.area_m2 / 1000.0 / mass
    gravity_acc = planet.gravity(radius)
    thrust_acc = 0.0
    mass_flow = 0.0
    if stage is not None and stage.thrust_kN > 0:
        # kN / t = m/s^2 and kN / (s * g0) = t/s.
        thrust_acc = stage.thrust_kN / mass
        mass_flow = stage.thrust_kN / (stage.isp_s * G0)
    pitch = math.radians(pitch_deg)
    radial_acc = gravity_acc + thrust_acc * math.sin(pitch)
    tangential_acc = thrust_acc * math.cos(pitch)
    if speed > 0 and drag_acc > 0:
        radial_acc -= drag_acc * radial / speed
        tangential_acc -= drag_acc * tangential / speed
    return (
        radial,
        tangential / radius,
        radial_acc,
        tangential_acc,
        -mass_flow,
    ), dynamic_pressure


def _rk4(state, stage, planet, pitch_deg, dt):
    k1, q1 = _state_derivative(state, stage, planet, pitch_deg)
    s2 = tuple(value + 0.5 * dt * delta for value, delta in zip(state, k1))
    k2, _ = _state_derivative(s2, stage, planet, pitch_deg)
    s3 = tuple(value + 0.5 * dt * delta for value, delta in zip(state, k2))
    k3, _ = _state_derivative(s3, stage, planet, pitch_deg)
    s4 = tuple(value + dt * delta for value, delta in zip(state, k3))
    k4, _ = _state_derivative(s4, stage, planet, pitch_deg)
    next_state = tuple(value + dt * (a + 2 * b + 2 * c + d) / 6
                       for value, a, b, c, d in zip(state, k1, k2, k3, k4))
    return next_state, q1


def simulate_ascent(stages: list[StageSpec], payload_t: float, *,
                    planet: Planet | None = None,
                    pitch: PitchProgram | None = None,
                    settings: AscentSettings | None = None) -> AscentResult:
    """Run the T1 planar 3DOF ascent skeleton.

    The state is (radius_m, downrange_rad, radial_velocity, tangential_velocity,
    mass_t).  Stages are burned in list order; a spent stage is jettisoned and
    the next stage ignites immediately.  The simulation stops at the last
    jettison, at max_time, or on a crash; it does not yet model coast/orbit
    insertion.
    """
    if not stages:
        raise ValueError('at least one stage is required')
    if payload_t < 0 or not math.isfinite(payload_t):
        raise ValueError('payload_t must be finite and >= 0')
    planet = planet or Planet()
    pitch = pitch or PitchProgram()
    settings = settings or AscentSettings()
    initial_mass = payload_t + sum(_stage_wet(stage) for stage in stages)
    state = (
        planet.radius + settings.launch_altitude_m,
        0.0,
        0.0,
        settings.initial_tangential_m_s,
        initial_mass,
    )
    result = AscentResult(stages=list(stages), payload_t=payload_t, planet=planet,
                          pitch=pitch, settings=settings, initial_mass_t=initial_mass,
                          final_mass_t=initial_mass)
    stage_index = 0
    remaining = stages[0].propellant_t
    time_s = 0.0
    while time_s < settings.max_time_s:
        stage = stages[stage_index] if stage_index < len(stages) else None
        if stage is None:
            result.stopped_reason = 'all_stages_burned'
            break
        if remaining <= 1e-12:
            mass_before = state[4]
            state = (state[0], state[1], state[2], state[3], state[4] - stage.dry_mass_t)
            result.stage_events.append(StageEvent(
                time_s=time_s, stage_index=stage_index, event='jettison',
                altitude_m=state[0] - planet.radius,
                mass_before_t=mass_before, mass_after_t=state[4]))
            stage_index += 1
            if stage_index >= len(stages):
                result.stopped_reason = 'all_stages_burned'
                break
            remaining = stages[stage_index].propellant_t
            continue
        pitch_deg = pitch.pitch_deg(state[0] - planet.radius)
        mass_flow = (stage.thrust_kN * 1000.0 / (stage.isp_s * G0) / 1000.0
                     if stage.thrust_kN > 0 else 0.0)
        step = settings.dt_s
        if mass_flow > 0:
            step = min(step, remaining / mass_flow)
        state, dynamic_pressure = _rk4(state, stage, planet, pitch_deg, step)
        time_s += step
        if mass_flow > 0:
            remaining = max(0.0, remaining - mass_flow * step)
        altitude = state[0] - planet.radius
        speed = math.hypot(state[2], state[3])
        acceleration = 0.0
        if stage.thrust_kN > 0:
            acceleration += stage.thrust_kN / state[4]
        acceleration += abs(planet.gravity(state[0]))
        sample = AscentSample(
            time_s=time_s, altitude_m=altitude, downrange_m=state[1] * planet.radius,
            radial_velocity_m_s=state[2], tangential_velocity_m_s=state[3],
            speed_m_s=speed, mass_t=state[4], dynamic_pressure_Pa=dynamic_pressure,
            acceleration_m_s2=acceleration, thrust_kN=stage.thrust_kN,
            stage_index=stage_index,
        )
        result.samples.append(sample)
        result.final = sample
        result.max_dynamic_pressure_Pa = max(result.max_dynamic_pressure_Pa, dynamic_pressure)
        result.max_acceleration_m_s2 = max(result.max_acceleration_m_s2, acceleration)
        if altitude < -1000:
            result.stopped_reason = 'crashed'
            break
    if not result.stopped_reason:
        result.stopped_reason = 'max_time'
    result.final_mass_t = state[4]
    return result
