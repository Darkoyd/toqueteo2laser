import global_data
import math
import random
from utils import calculate_geometric_center
import numpy as np
from collections import defaultdict


class Effect():

    def __init__(self, name, level):
        self.name = name
        self.level = level


class LaserPoint():

    def __init__(self, x, y):
        self.x = x
        self.y = y
        
        self.r = 0
        self.g = 100
        self.b = 0

    def set_color(self, r, g, b):
        self.r = r
        self.g = g
        self.b = b

    def is_blank(self):
        if self.r == 0 and self.g == 0 and self.b == 0:
            return True
        else:
            return False

    def __str__(self):
        return ('X:' + str(self.x) + ', Y:' + str(self.y) + ', R:' + str(self.r) + ', G:' + str(self.g) + ', B:' + str(self.b) + ', Blank: ' + str(self.is_blank()))


class LaserObject():    
    point_list = []
    effects = []
    group = 0

    def has_effect(self, effect_name) -> bool:
        for effect in self.effects:
            if effect.name == effect_name:
                return True
        return False
            
    def apply_rotation(self, rotation_speed):
        center_x, center_y = calculate_geometric_center(self.point_list)
        rotation_radians = math.radians(rotation_speed)

        for point in self.point_list:
            dx, dy = point.x - center_x, point.y - center_y
            point.x = center_x + dx * math.cos(rotation_radians) - dy * math.sin(rotation_radians)
            point.y = center_y + dx * math.sin(rotation_radians) + dy * math.cos(rotation_radians)

    def get_effect_level(self, effect_name, default=0.0):
        for effect in self.effects:
            if effect.name == effect_name:
                return float(effect.level)
        return default

    def apply_point_perspective(self, raw_points):
        # Fetch perspective parameters from the effects list
        pitch = self.get_effect_level('PERSPECTIVE_PITCH', 0.0)
        yaw = self.get_effect_level('PERSPECTIVE_YAW', 0.0)
        roll = self.get_effect_level('PERSPECTIVE_ROLL', 0.0)
        tx = self.get_effect_level('PERSPECTIVE_TX', 0.0)
        ty = self.get_effect_level('PERSPECTIVE_TY', 0.0)
        tz = self.get_effect_level('PERSPECTIVE_TZ', 0.0)
        show_square = self.get_effect_level('PERSPECTIVE_SHOW_SQUARE', 0.0)

        # Skip heavy math if the plane hasn't been moved AND we don't need the square
        if all(v == 0.0 for v in [pitch, yaw, roll, tx, ty, tz]) and show_square < 0.5:
            return raw_points

        width = int(global_data.config['laser_output']['width'])
        height = int(global_data.config['laser_output']['height'])
        x_origin = width / 2.0
        y_origin = height / 2.0
        scale = min(width, height) / 4.0

        # Copy raw points so we don't permanently modify the original list
        points_to_transform = list(raw_points)

        # Inject the dense reference square points if the toggle is ON
        if show_square > 0.5:
            # Calculate the 1/16th area bounds (vertices at +/- 1/4 in normalized space)
            quarter_scale = scale * 0.25
            x_min = int(x_origin - quarter_scale)
            x_max = int(x_origin + quarter_scale)
            y_min = int(y_origin - quarter_scale)
            y_max = int(y_origin + quarter_scale)
            
            step = 15  # Point density
            
            # Top edge
            for x in range(x_min, x_max, step): points_to_transform.append((x, y_min))
            # Right edge
            for y in range(y_min, y_max, step): points_to_transform.append((x_max, y))
            # Bottom edge
            for x in range(x_max, x_min, -step): points_to_transform.append((x, y_max))
            # Left edge
            for y in range(y_max, y_min, -step): points_to_transform.append((x_min, y))
            
            # Close the square
            points_to_transform.append((x_min, y_min))

        # Build 3D Euler Rotation Matrix
        cx, sx = np.cos(pitch), np.sin(pitch)
        cy, sy = np.cos(yaw), np.sin(yaw)
        cz, sz = np.cos(roll), np.sin(roll)

        Rx = np.array([[1, 0, 0], [0, cx, -sx], [0, sx, cx]])
        Ry = np.array([[cy, 0, sy], [0, 1, 0], [-sy, 0, cy]])
        Rz = np.array([[cz, -sz, 0], [sz, cz, 0], [0, 0, 1]])
        R = Rz @ Ry @ Rx

        T = np.array([tx, ty, tz - 1.0])  # Object-local rotation, pushed in front of camera

        x_min, x_max, y_min, y_max = inner_box(width, height)
        transformed_points = []
        for x, y in points_to_transform:
            U = (x - x_origin) / scale
            V = (y - y_origin) / scale
            P = np.array([U, V, 0.0])

            P_world = R @ P + T

            if P_world[2] >= -0.01:
                continue

            u_proj = -P_world[0] / P_world[2]
            v_proj = -P_world[1] / P_world[2]

            x_new = u_proj * scale + x_origin
            y_new = v_proj * scale + y_origin
            
            if x_min <= x_new <= x_max and y_min <= y_new <= y_max:
                transformed_points.append((x_new, y_new))

        return transformed_points

    def sort_path(self, raw_points, color_rgb):
        if not raw_points:
            width = int(global_data.config['laser_output']['width'])
            height = int(global_data.config['laser_output']['height'])
            fallback = LaserPoint(int(width / 2.0), int(height / 2.0))
            fallback.set_color(0, 0, 0)
            return [fallback]

        pts_array = np.array(raw_points, dtype=float)
        n_points = len(pts_array)
        visited = np.zeros(n_points, dtype=bool)

        current_idx = 0
        visited[current_idx] = True
        
        sorted_points = []
        current_pt = pts_array[current_idx]
        
        pt = LaserPoint(int(current_pt[0]), int(current_pt[1]))
        pt.set_color(*color_rgb)
        sorted_points.append(pt)

        jump_threshold_sq = 22500 
        dwell_count = 20

        for _ in range(n_points - 1):
            # Nearest unvisited point
            # ponytail: O(n²) greedy walk, fine for a few thousand points; KD-tree with removal if curves get denser
            dist_sq = ((pts_array - current_pt) ** 2).sum(axis=1)
            dist_sq[visited] = np.inf
            next_idx = dist_sq.argmin()
            best_dist_sq = dist_sq[next_idx]

            visited[next_idx] = True
            next_pt = pts_array[next_idx]

            if best_dist_sq > jump_threshold_sq:
                for _ in range(dwell_count):
                    off_current = LaserPoint(int(current_pt[0]), int(current_pt[1]))
                    off_current.set_color(0, 0, 0)
                    sorted_points.append(off_current)
                
                for _ in range(dwell_count):
                    off_next = LaserPoint(int(next_pt[0]), int(next_pt[1]))
                    off_next.set_color(0, 0, 0)
                    sorted_points.append(off_next)

            pt = LaserPoint(int(next_pt[0]), int(next_pt[1]))
            pt.set_color(*color_rgb)
            sorted_points.append(pt)

            current_pt = next_pt

        return sorted_points
            
    def update(self):
        for effect in self.effects:
            if effect.name == 'ROTATION_SPEED':
                self.apply_rotation(effect.level/255)
    
    def __str__(self):
        return('LaserObject, type: ' + str(type(self)) + ', group:' + str(self.group) + ', effects:' + str(self.effects))


class Blank(LaserObject):

    def __init__(self, group = 0):
        self.point_list = []
        self.group = group

        blank_point = LaserPoint(0, 0)
        blank_point.set_color(0, 0, 0)
        self.point_list.append(blank_point)


class StaticLine(LaserObject):

    def __init__(self, from_point, to_point, group = 0):
        self.point_list = []
        self.group = group
        
        self.point_list.append(from_point)
        self.point_list.append(to_point)     


class StaticPoint(LaserObject):
    def __init__(self, target_point,group = 0):
        self.group = group

        self.point_list = []
        self.point_list.append(target_point)

 
class StaticCircle(LaserObject):
    def __init__(self, center_x, center_y, radius, r, g, b, group=0):
        super().__init__()

        self.point_list = []
        self.group = group

        # Number of points to create the circle
        points_count = 100  # This can be adjusted for smoother circles

        # Calculate the angle step size
        step_size = 2 * math.pi / points_count

        # Create points
        for i in range(points_count + 1):  # +1 to close the circle
            t = step_size * i
            x = int(round(radius * math.cos(t) + center_x, 0))
            y = int(round(radius * math.sin(t) + center_y, 0))
            laser_point = LaserPoint(x, y)
            laser_point.set_color(r, g, b)
            self.point_list.append(laser_point)

        # Ensure the last point is the same as the first to close the circle
        self.point_list.append(self.point_list[0])


class StaticWave(LaserObject):
    from models import LaserPoint

    def __init__(self, group = 0):
        self.point_list = []
        self.group = group
        
        # Set up wave properties
        self.wave_length = int(global_data.config['laser_output']['width'])
        self.amplitude = 500        
        self.frequency = 3
        self.vertical_shift = int(global_data.config['laser_output']['height']) / 2
        
        self.draw_wave()

    def draw_wave(self):
        self.point_list = []
        for x in range(0, int(global_data.config['laser_output']['width']), 50):
            y = math.sin((2 * math.pi * self.frequency * x) / self.wave_length) * self.amplitude + self.vertical_shift
            laser_point = LaserPoint(int(x), int(y))
            laser_point.set_color(0, 0, 255)
            self.point_list.append(laser_point)
            
    def update(self):
        super().update()
        if 'wave_amplitude' in global_data.parameters and self.amplitude != global_data.parameters['wave_amplitude']:
            self.amplitude = global_data.parameters['wave_amplitude']
            self.draw_wave()
            
        if 'wave_length' in global_data.parameters and self.wave_length != global_data.parameters['wave_length']:
            self.wave_length = global_data.parameters['wave_length']
            self.draw_wave()
        
        """    
        if 'wave_frequency' in global_data.parameters and self.frequency != global_data.parameters['wave_frequency']:
            self.frequency = global_data.parameters['wave_frequency']
            self.draw_wave()
        """


class AnimatedWave(StaticWave):
    def __init__(self, group=0, animation_speed=0.5, amplitude_mod=0, frequency_mod=0, noise_intensity=0):
        super().__init__(group)
        
        self.point_list = []
        self.group = group
        
        self.animation_progress = 0
        self.animation_speed = animation_speed
        self.amplitude_mod = amplitude_mod
        self.frequency_mod = frequency_mod
        self.noise_intensity = noise_intensity

    def update(self):
        self.animation_progress += self.animation_speed
        
        if 'wave_speed' in global_data.parameters and self.animation_speed != global_data.parameters['wave_speed']:
            self.animation_speed = global_data.parameters['wave_speed']

        raw_points = []
        for x in range(0, int(self.wave_length), 50):
            # Varying amplitude and frequency with parameters
            varied_amplitude = self.amplitude + math.sin(x / 100.0) * self.amplitude_mod
            varied_frequency = self.frequency + math.sin(x / 200.0) * self.frequency_mod

            # Base sine wave
            y = math.sin((2 * math.pi * varied_frequency * (x - self.animation_progress)) / self.wave_length) * varied_amplitude

            if self.amplitude_mod != 0 or self.frequency_mod != 0:
                y += math.sin((4 * math.pi * varied_frequency * (x - self.animation_progress)) / self.wave_length) * (varied_amplitude / 3)

            if self.noise_intensity != 0:
                y += random.uniform(-self.noise_intensity, self.noise_intensity)

            y += self.vertical_shift
            raw_points.append((int(x), int(y)))

        # 1. Apply perspective transform to raw coordinates
        projected_points = self.apply_point_perspective(raw_points)

        # 2. Rebuild point list with laser colors
        self.point_list = []
        for px, py in projected_points:
            laser_point = LaserPoint(int(px), int(py))
            laser_point.set_color(0, 0, 255)
            self.point_list.append(laser_point)
            
        super().update()
        

class StaticStars(LaserObject):
    def __init__(self, group=0):
        super().__init__()
        self.group = group
        self.star_count = int(global_data.parameters.get('stars_amount', 8))  # Default to 50 stars if not specified
        self.generate_stars()

    def generate_stars(self):
        self.point_list = []
        for _ in range(self.star_count):
            # Add the actual star point
            x = random.randint(0, int(global_data.config['laser_output']['width']))
            y = random.randint(0, int(global_data.config['laser_output']['height']))
            # Add a blank point after each star to ensure the laser is off when moving to the next point
            blank_point = LaserPoint(x, y)
            blank_point.set_color(0, 0, 0)
            self.point_list.append(blank_point)
            
            star_point = LaserPoint(x, y)
            star_point.set_color(255, 255, 255)  # White color for stars
            self.point_list.append(star_point)

            # Add a blank point after each star to ensure the laser is off when moving to the next point
            #blank_point = LaserPoint(x, y)
            #blank_point.set_color(0, 0, 0)
            #self.point_list.append(blank_point)
            

    def update(self):
        if 'stars_amount' in global_data.parameters and self.star_count != global_data.parameters['stars_amount']:
            self.star_count = int(global_data.parameters['stars_amount'])
            self.generate_stars()
        super().update()

# ==========================================
# HOMOGRAPHY MATH HELPERS
# ==========================================
def get_inverse_homography(pitch, yaw, roll, tx, ty, tz):
    """Compute the inverse Homography matrix M from 6 spatial parameters."""
    cx, sx = np.cos(pitch), np.sin(pitch)
    cy, sy = np.cos(yaw), np.sin(yaw)
    cz, sz = np.cos(roll), np.sin(roll)

    # 3D Rotation matrices
    Rx = np.array([[1, 0, 0], [0, cx, -sx], [0, sx, cx]])
    Ry = np.array([[cy, 0, sy], [0, 1, 0], [-sy, 0, cy]])
    Rz = np.array([[cz, -sz, 0], [sz, cz, 0], [0, 0, 1]])
    R = Rz @ Ry @ Rx

    # Construct standard projection Homography (Z=0 plane mapped through camera)
    # Note: We add 1.0 to tz so that tz=0 defaults to a neutral Z-distance scale of 1.0
    H = np.array([
        [R[0,0], R[0,1], tx],
        [R[1,0], R[1,1], ty],
        [R[2,0], R[2,1], tz + 1.0] 
    ])
    
    try:
        M = np.linalg.inv(H)
    except np.linalg.LinAlgError:
        M = np.eye(3) # Fallback to Identity if perfectly singular
    return M

def mul_poly(p1, p2):
    res = defaultdict(float)
    for (i1, j1, k1), c1 in p1.items():
        for (i2, j2, k2), c2 in p2.items():
            res[(i1+i2, j1+j2, k1+k2)] += c1 * c2
    return res

def transform_poly(poly, M):
    """Applies inverse Homography M to a homogeneous polynomial {(i, j, k): coeff of x^i y^j z^k}
    by substituting (x, y, z) = M @ (u, v, w)."""
    XYZ = [{(1,0,0): M[r,0], (0,1,0): M[r,1], (0,0,1): M[r,2]} for r in range(3)]
    new_poly = defaultdict(float)
    for powers, coeff in poly.items():
        term = {(0,0,0): coeff}
        for form, n in zip(XYZ, powers):
            for _ in range(n):
                term = mul_poly(term, form)
        for key, val in term.items():
            new_poly[key] += val
    return new_poly

def real_roots(poly, degree, t):
    """Real roots v of poly(t, v, 1) = 0."""
    coeffs = [0.0] * (degree + 1)
    for (i, j, k), val in poly.items():
        coeffs[degree - j] += val * (t ** i)
    while len(coeffs) > 1 and abs(coeffs[0]) < 1e-12:
        coeffs.pop(0)
    return [r.real for r in np.roots(coeffs) if abs(r.imag) < 1e-6]

def inner_box(width, height):
    """Drawing area: the centre quarter of the output, as (x_min, x_max, y_min, y_max)."""
    return int(width * 0.25), int(width * 0.75), int(height * 0.25), int(height * 0.75)

def deduplicate_points(points, min_dist=5.0):
    """Drops points closer than min_dist to the last kept one."""
    if not points:
        return []
    filtered = [points[0]]
    for x2, y2 in points[1:]:
        x1, y1 = filtered[-1]
        if (x2 - x1) ** 2 + (y2 - y1) ** 2 >= min_dist ** 2:
            filtered.append((x2, y2))
    return filtered

def generate_homography_grid_lines(M, width, height, step=50):
    x_origin = width / 2.0
    y_origin = height / 2.0
    scale = min(width, height) / 4.0
    x_min, x_max, y_min, y_max = inner_box(width, height)
    
    base_lines = [
        np.array([1.0, 0.0,  0.5]),
        np.array([1.0, 0.0, -0.5]),
        np.array([0.0, 1.0,  0.5]),
        np.array([0.0, 1.0, -0.5])
    ]
    
    MT = M.T
    grid_lines = []

    for l in base_lines:
        l_prime = MT @ l
        a, b, c = l_prime[0], l_prime[1], l_prime[2]
        line_points = []

        if abs(b) >= abs(a):
            if abs(b) < 1e-6: continue
            for x in range(x_min, x_max + step, step):
                u = (x - x_origin) / scale
                v = -(a * u + c) / b
                y_val = v * scale + y_origin
                if y_min <= y_val <= y_max:
                    line_points.append((x, int(y_val)))
        else:
            if abs(a) < 1e-6: continue
            for y in range(y_min, y_max + step, step):
                v = (y - y_origin) / scale
                u = -(b * v + c) / a
                x_val = u * scale + x_origin
                if x_min <= x_val <= x_max:
                    line_points.append((int(x_val), y))
        
        if line_points:
            grid_lines.append(line_points)

    return grid_lines

def create_laser_line_with_dwells(points, color_rgb, dwell_count=25):
    """Converts ordered 2D points into LaserPoints with blanking dwell at start and end."""
    if not points:
        return []

    laser_pts = []
    start_x, start_y = points[0]
    end_x, end_y = points[-1]

    # Blank dwell at the start (gives galvo time to move to start position with laser off)
    for _ in range(dwell_count):
        pt = LaserPoint(int(start_x), int(start_y))
        pt.set_color(0, 0, 0)
        laser_pts.append(pt)

    # Active visible path points
    for x, y in points:
        pt = LaserPoint(int(x), int(y))
        pt.set_color(*color_rgb)
        laser_pts.append(pt)

    # Blank dwell at the end (turns laser off before next move)
    for _ in range(dwell_count):
        pt = LaserPoint(int(end_x), int(end_y))
        pt.set_color(0, 0, 0)
        laser_pts.append(pt)

    return laser_pts

def generate_homography_horizon_line(M, width, height, step=50):
    x_origin = width / 2.0
    y_origin = height / 2.0
    scale = min(width, height) / 4.0
    x_min, x_max, y_min, y_max = inner_box(width, height)

    # Extract line coefficients directly from the 3rd row of matrix M
    a = M[2, 0]
    b = M[2, 1]
    c = M[2, 2]

    # If both a and b are nearly 0, the plane is untilted and the horizon is at infinity
    if abs(a) < 1e-6 and abs(b) < 1e-6:
        return []

    # Convert normalized line coefficients to pixel-space equation: A*x + B*y + C = 0
    A = a
    B = b
    C = c * scale - a * x_origin - b * y_origin

    horizon_points = []

    # Sample along the dominant axis to ensure continuous, even point spacing
    if abs(B) >= abs(A):
        for x in range(x_min, x_max + step, step):
            y_val = -(A * x + C) / B
            if y_min <= y_val <= y_max:
                horizon_points.append((x, int(y_val)))
    else:
        for y in range(y_min, y_max + step, step):
            x_val = -(B * y + C) / A
            if x_min <= x_val <= x_max:
                horizon_points.append((int(x_val), y))

    return horizon_points


def homography_overlay(obj, M, width, height):
    """Grid lines (white) and horizon (green) of the homography plane, appended without sort_path."""
    overlay = []
    for line in generate_homography_grid_lines(M, width, height):
        projected_line = obj.apply_point_perspective(line)
        if projected_line:
            overlay.extend(create_laser_line_with_dwells(projected_line, color_rgb=(255, 255, 255), dwell_count=20))

    horizon_pts = generate_homography_horizon_line(M, width, height)
    if horizon_pts:
        projected_horizon = obj.apply_point_perspective(horizon_pts)
        if projected_horizon:
            overlay.extend(create_laser_line_with_dwells(projected_horizon, color_rgb=(0, 255, 0), dwell_count=20))
    return overlay


# ==========================================
# CLASS IMPLEMENTATIONS
# ==========================================

class AlgebraicCurve(LaserObject):
    """Curve poly(x, y) = 0 in normalized screen space, seen through the homography knobs.
    Subclasses set degree, step (sampling density), color and defaults (OSC parameters of base_poly)."""
    HOMOGRAPHY = ('homography_pitch', 'homography_yaw', 'homography_roll',
                  'homography_tx', 'homography_ty', 'homography_tz', 'homography_show_square')
    PERSPECTIVE = ('PERSPECTIVE_PITCH', 'PERSPECTIVE_YAW', 'PERSPECTIVE_ROLL',
                   'PERSPECTIVE_TX', 'PERSPECTIVE_TY', 'PERSPECTIVE_TZ', 'PERSPECTIVE_SHOW_SQUARE')

    def __init__(self, group=0):
        self.group = group
        self.state = None
        self.update()

    def base_poly(self, a):
        """Parameters named ..._a<i><j> are the x^i y^j coefficients."""
        return {(int(k[-2]), int(k[-1]), self.degree - int(k[-2]) - int(k[-1])): v for k, v in a.items()}

    def update(self):
        super().update()
        a = {k: float(global_data.parameters.get(k, d)) for k, d in self.defaults.items()}
        h = [float(global_data.parameters.get(k, 0.0)) for k in self.HOMOGRAPHY]
        state = (a, h, [self.get_effect_level(e) for e in self.PERSPECTIVE])
        if state != self.state:
            self.state = state
            self.draw(a, h)

    def draw(self, a, h):
        width = int(global_data.config['laser_output']['width'])
        height = int(global_data.config['laser_output']['height'])
        x_origin = width / 2.0
        y_origin = height / 2.0
        scale = min(width, height) / 4.0
        x_min, x_max, y_min, y_max = inner_box(width, height)

        M = get_inverse_homography(*h[:6])
        poly = transform_poly(self.base_poly(a), M)
        swapped = {(j, i, k): c for (i, j, k), c in poly.items()}

        # Sweep X axis solving for V, then Y axis solving for U
        curve_points = []
        for x in range(x_min, x_max + self.step, self.step):
            for v in real_roots(poly, self.degree, (x - x_origin) / scale):
                y_val = v * scale + y_origin
                if y_min <= y_val <= y_max:
                    curve_points.append((x, int(y_val)))
        for y in range(y_min, y_max + self.step, self.step):
            for u in real_roots(swapped, self.degree, (y - y_origin) / scale):
                x_val = u * scale + x_origin
                if x_min <= x_val <= x_max:
                    curve_points.append((int(x_val), y))

        projected_curve = deduplicate_points(self.apply_point_perspective(curve_points))
        final_point_list = self.sort_path(projected_curve, color_rgb=self.color)
        if h[6] > 0.5:
            final_point_list.extend(homography_overlay(self, M, width, height))
        self.point_list = final_point_list


class Cubic(AlgebraicCurve):
    degree, step, color = 3, 10, (255, 255, 0)
    defaults = {'cubic_a00': 0.0, 'cubic_a10': 1.0, 'cubic_a01': 0.0, 'cubic_a20': 0.0, 'cubic_a11': 0.0,
                'cubic_a02': 1.0, 'cubic_a30': -1.0, 'cubic_a21': 0.0, 'cubic_a12': 0.0, 'cubic_a03': 0.0}


class Conic(AlgebraicCurve):
    degree, step, color = 2, 10, (255, 255, 0)
    defaults = {'conic_a00': -0.25, 'conic_a10': 0.0, 'conic_a01': 0.0,
                'conic_a20': 1.0, 'conic_a11': 0.0, 'conic_a02': 1.0}


class Parabola(Conic):
    """y = a (x - h)^2 + k as a conic, so it stays a parabola whatever the knobs do (screen y points down)."""
    defaults = {'parabola_a': 1.0, 'parabola_c': 0.0, 'parabola_b': 0.0}  # scaling, vertex x, vertex y

    def base_poly(self, a):
        s, h, k = a['parabola_a'], a['parabola_c'], a['parabola_b']
        return {(2, 0, 0): s, (1, 0, 1): -2 * s * h, (0, 0, 2): s * h * h + k, (0, 1, 1): -1.0}


class Hyperelliptic(AlgebraicCurve):
    degree, step, color = 8, 10, (255, 255, 0)
    defaults = {'hyperelliptic_a8': 0.1, 'hyperelliptic_a7': 0.0, 'hyperelliptic_a6': -2.0,
                'hyperelliptic_a5': 0.0, 'hyperelliptic_a4': 12.0, 'hyperelliptic_a3': 0.0,
                'hyperelliptic_a2': -20.0, 'hyperelliptic_a1': 0.0, 'hyperelliptic_a0': 8.0}

    def base_poly(self, a):
        """y^2 = sum_k a_k x^k, homogenized to degree 8."""
        poly = {(k, 0, 8 - k): -a[f'hyperelliptic_a{k}'] for k in range(9)}
        poly[(0, 2, 6)] = 1.0
        return poly


class SvgObject(LaserObject):
    """Strokes of every path/shape in an .svg file (outlines, not fills), fitted into inner_box and seen
    through the homography knobs like the curves. Text must be converted to paths first (Inkscape: Path >
    Object to Path); single-stroke fonts (Inkscape Extensions > Text > Hershey Text) draw each letter once
    instead of twice around its outline."""
    step = 16        # output units between samples along a path (lower = denser, sharper corners, slower frame)
    dwell_count = 6   # blank points at the start/end of each subpath

    def __init__(self, filename, group=0):
        from svgelements import SVG, Shape, Path
        self.group = group
        self.state = None
        subpaths = []
        for el in SVG.parse(filename).elements():
            if not isinstance(el, Shape):
                continue
            paint = el.stroke if el.stroke is not None and el.stroke.value is not None else el.fill
            color = (paint.red, paint.green, paint.blue) if paint is not None and paint.value is not None else (0, 255, 0)
            for sub in Path(el).as_subpaths():
                sub = Path(sub)
                if sub.length() > 0:
                    subpaths.append((color, sub))

        # Sampled once, in the curves' normalized plane coordinates (inner_box = -1..1)
        width = int(global_data.config['laser_output']['width'])
        height = int(global_data.config['laser_output']['height'])
        x_min, x_max, y_min, y_max = inner_box(width, height)
        self.strokes = []  # [(color, (n, 2) array), ...]
        if subpaths:
            boxes = [sub.bbox() for _, sub in subpaths]
            bx0, by0 = min(b[0] for b in boxes), min(b[1] for b in boxes)
            bx1, by1 = max(b[2] for b in boxes), max(b[3] for b in boxes)
            k = min((x_max - x_min) / max(bx1 - bx0, 1e-9), (y_max - y_min) / max(by1 - by0, 1e-9))
            ox = (x_min + x_max - (bx1 - bx0) * k) / 2 - bx0 * k  # centred
            oy = (y_min + y_max - (by1 - by0) * k) / 2 - by0 * k
            origin, scale = np.array([width / 2.0, height / 2.0]), min(width, height) / 4.0
            for color, sub in subpaths:
                # ponytail: sampled before projection, so a strong zoom spreads points apart; resample after if it shows
                n = max(2, int(sub.length() * k / self.step) + 1)
                pts = np.asarray(sub.npoint(np.linspace(0, 1, n))) * k + (ox, oy)
                self.strokes.append((color, (pts - origin) / scale))
        self.update()

    def update(self):
        super().update()
        h = [float(global_data.parameters.get(k, 0.0)) for k in AlgebraicCurve.HOMOGRAPHY]
        state = (h, [self.get_effect_level(e) for e in AlgebraicCurve.PERSPECTIVE])
        if state != self.state:
            self.state = state
            self.draw(h)

    def draw(self, h):
        width = int(global_data.config['laser_output']['width'])
        height = int(global_data.config['laser_output']['height'])
        x_min, x_max, y_min, y_max = inner_box(width, height)
        origin, scale = np.array([width / 2.0, height / 2.0]), min(width, height) / 4.0
        M = get_inverse_homography(*h[:6])
        H = np.linalg.inv(M)  # plane -> screen (M maps screen -> plane for the curve coefficients)

        # Built locally and swapped in once: the laser and preview threads both render this object
        point_list = []
        for color, plane in self.strokes:
            uvw = np.c_[plane, np.ones(len(plane))] @ H.T
            w = uvw[:, 2]
            visible = w > 1e-6  # in front of the camera
            screen = uvw[:, :2] / np.where(visible, w, 1.0)[:, None] * scale + origin
            lo, hi = np.array([x_min, y_min]) - 0.5, np.array([x_max, y_max]) + 0.5  # slack: the fit touches the box edges
            visible &= ((screen >= lo) & (screen <= hi)).all(axis=1)
            # Break the stroke wherever it leaves inner_box or crosses the horizon
            run = []
            for pt, ok in zip(screen.tolist(), visible):
                if ok:
                    run.append(pt)
                elif run:
                    point_list.extend(create_laser_line_with_dwells(self.apply_point_perspective(run), color, self.dwell_count))
                    run = []
            if run:
                point_list.extend(create_laser_line_with_dwells(self.apply_point_perspective(run), color, self.dwell_count))
        if h[6] > 0.5:
            point_list.extend(homography_overlay(self, M, width, height))
        self.point_list = point_list or Blank().point_list  # empty if the knobs pushed everything off screen
