import bpy
import math
import os
import random
import json
import time
import urllib.error
import urllib.request
from mathutils import Vector
from bpy.app.handlers import persistent

# =====================================================
# RAILWAY CROSSING 3D SIMULATION FOR BLENDER
# =====================================================
# How to use:
# 1. Open Blender.
# 2. Go to the Scripting workspace.
# 3. Open this file or paste the code into a new text block.
# 4. Press Run Script.
#
# Animation behavior:
# - The train approaches the crossing.
# - Warning lights begin before the train reaches the road.
# - The railway gates close fully before the train arrives.
# - Vehicles stop at the stop bars and do not move while the gate is closed.
# - Gates reopen only after the train has cleared the crossing.
# - Vehicles move again after the gates are open.
# - Optional ThingsBoard telemetry can be enabled near the top of the file.


# =====================================================
# TIMING
# =====================================================
FRAME_START = 1
WARNING_START = 55
GATE_DOWN = 110
TRAIN_CLEAR = 305
GATE_UP = 350
TRAFFIC_RESUME = 355
FRAME_END = 430
FPS = 30

# Toll plaza layout (east service road).
TOLL_BOOTH_X = 58
TOLL_ROAD_Y = 43
TOLL_LANE_Y = TOLL_ROAD_Y + 2.1
TOLL_VEHICLE_LANE_Y = TOLL_ROAD_Y + 2.55
TOLL_DETECTION_GANTRY_X = 50
MAIN_ROAD_LANE_X = 0.0
MAIN_ROAD_WIDTH = 6.5
BUS_LANE_Y = -43
BUS_STOP_X = -58
DELIVERY_HUB_X = -70
DELIVERY_HUB_Y = 50
RENDER_OUTPUT_DIR = "//renders"
RENDER_FILE_PREFIX = "railway_crossing_"


# =====================================================
# IOT / THINGSBOARD SETTINGS
# =====================================================
THINGSBOARD_ENABLED = False
THINGSBOARD_HOST = "https://demo.thingsboard.io"
THINGSBOARD_ACCESS_TOKEN = "PUT_DEVICE_ACCESS_TOKEN_HERE"
TELEMETRY_SEND_EVERY_N_FRAMES = 15
TELEMETRY_TIMEOUT_SECONDS = 1.5

STORE_TELEMETRY_IN_BLENDER_TEXT = True
EXPORT_GLB_ON_RUN = False
GLB_EXPORT_PATH = "//railway_crossing_iot_scene.glb"


# =====================================================
# SCENE CLEANUP
# =====================================================
def clear_scene():
    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.object.delete()

    for datablock in (
        bpy.data.meshes,
        bpy.data.materials,
        bpy.data.lights,
        bpy.data.cameras,
        bpy.data.curves,
        bpy.data.actions,
    ):
        for item in list(datablock):
            datablock.remove(item, do_unlink=True)


clear_scene()

scene = bpy.context.scene
scene.frame_start = FRAME_START
scene.frame_end = FRAME_END
scene.frame_set(FRAME_START)
scene.render.fps = FPS
scene.render.resolution_x = 1920
scene.render.resolution_y = 1080
scene.render.film_transparent = False

# --- FIX: Render path fallback when .blend file is unsaved ---
if bpy.data.filepath:
    render_abs_dir = bpy.path.abspath(RENDER_OUTPUT_DIR)
else:
    render_abs_dir = os.path.join(os.path.expanduser("~"), "BlenderRenders")
    print(f"[INFO] Blend file not saved — using fallback render dir: {render_abs_dir}")

os.makedirs(render_abs_dir, exist_ok=True)
scene.render.filepath = render_abs_dir + os.sep + RENDER_FILE_PREFIX
scene.render.use_file_extension = True
try:
    scene.render.image_settings.file_format = "PNG"
except Exception:
    pass

try:
    scene.render.engine = "CYCLES"
    scene.cycles.samples = 128
    scene.cycles.use_denoising = True
except Exception:
    try:
        scene.render.engine = "BLENDER_EEVEE_NEXT"
    except Exception:
        scene.render.engine = "BLENDER_EEVEE"

# --- ENHANCED COLOR GRADING ---
try:
    scene.view_settings.view_transform = "Filmic"
    scene.view_settings.look = "Very High Contrast"
    scene.view_settings.exposure = 0.35
    scene.view_settings.gamma = 1.05
except Exception:
    try:
        scene.view_settings.look = "High Contrast"
        scene.view_settings.exposure = 0.3
        scene.view_settings.gamma = 1.05
    except Exception:
        pass

# --- COMPOSITOR: Bloom / Glare ---
# Some Blender 5.1 builds do not expose Scene.node_tree from Python. The
# compositor is a polish layer only, so skip it safely if unavailable.
def setup_scene_compositor(scene_ref):
    try:
        if hasattr(scene_ref, "use_nodes"):
            scene_ref.use_nodes = True

        comp_tree = getattr(scene_ref, "node_tree", None)
        if comp_tree is None:
            print("[INFO] Scene compositor node_tree unavailable; skipping compositor glow.")
            return False

        for node in list(comp_tree.nodes):
            comp_tree.nodes.remove(node)

        render_layers_node = comp_tree.nodes.new("CompositorNodeRLayers")
        render_layers_node.location = (0, 300)

        glare_node = comp_tree.nodes.new("CompositorNodeGlare")
        glare_node.location = (300, 300)
        glare_node.glare_type = "FOG_GLOW"
        glare_node.quality = "HIGH"
        glare_node.threshold = 0.85
        glare_node.size = 7

        color_balance_node = comp_tree.nodes.new("CompositorNodeColorBalance")
        color_balance_node.location = (550, 300)
        color_balance_node.correction_method = "LIFT_GAMMA_GAIN"
        color_balance_node.lift = (0.96, 0.97, 1.02)
        color_balance_node.gamma = (1.02, 1.0, 0.98)
        color_balance_node.gain = (1.08, 1.05, 1.02)

        composite_node = comp_tree.nodes.new("CompositorNodeComposite")
        composite_node.location = (800, 300)

        viewer_node = comp_tree.nodes.new("CompositorNodeViewer")
        viewer_node.location = (800, 100)

        comp_tree.links.new(render_layers_node.outputs["Image"], glare_node.inputs["Image"])
        comp_tree.links.new(glare_node.outputs["Image"], color_balance_node.inputs["Image"])
        comp_tree.links.new(color_balance_node.outputs["Image"], composite_node.inputs["Image"])
        comp_tree.links.new(color_balance_node.outputs["Image"], viewer_node.inputs["Image"])
        return True
    except Exception as exc:
        print(f"[INFO] Compositor setup skipped safely: {exc}")
        return False


COMPOSITOR_ENABLED = setup_scene_compositor(scene)


# =====================================================
# HELPERS
# =====================================================
def set_input(node, names, value):
    for name in names:
        if name in node.inputs:
            node.inputs[name].default_value = value
            return node.inputs[name]
    return None


def make_mat(name, color, metallic=0.0, roughness=0.55, emission=None, emission_strength=0.0):
    mat = bpy.data.materials.new(name)
    mat.diffuse_color = color
    mat.use_nodes = True
    bsdf = mat.node_tree.nodes.get("Principled BSDF")
    if bsdf:
        set_input(bsdf, ["Base Color"], color)
        set_input(bsdf, ["Metallic"], metallic)
        set_input(bsdf, ["Roughness"], roughness)
        if emission:
            set_input(bsdf, ["Emission Color", "Emission"], emission)
            set_input(bsdf, ["Emission Strength"], emission_strength)
    return mat


def make_transparent_mat(name, color, alpha=0.28, emission=None, emission_strength=0.0):
    mat = make_mat(name, (color[0], color[1], color[2], alpha), roughness=0.35, emission=emission, emission_strength=emission_strength)
    mat.diffuse_color = (color[0], color[1], color[2], alpha)
    bsdf = mat.node_tree.nodes.get("Principled BSDF")
    if bsdf:
        set_input(bsdf, ["Alpha"], alpha)
    try:
        mat.blend_method = "BLEND"
        mat.use_screen_refraction = True
        mat.show_transparent_back = True
    except Exception:
        pass
    return mat


def add_cube(name, location, dimensions, material=None, rotation=(0, 0, 0)):
    bpy.ops.mesh.primitive_cube_add(size=1, location=location, rotation=rotation)
    obj = bpy.context.object
    obj.name = name
    obj.dimensions = dimensions
    bpy.ops.object.transform_apply(location=False, rotation=False, scale=True)
    if material:
        obj.data.materials.append(material)
    return obj


def add_torus(name, location, major_radius, minor_radius, material=None, rotation=(0, 0, 0)):
    bpy.ops.mesh.primitive_torus_add(
        major_radius=major_radius,
        minor_radius=minor_radius,
        major_segments=72,
        minor_segments=12,
        location=location,
        rotation=rotation,
    )
    obj = bpy.context.object
    obj.name = name
    if material:
        obj.data.materials.append(material)
    try:
        bpy.ops.object.shade_smooth()
    except Exception:
        pass
    return obj


def add_cylinder(name, location, radius, depth, material=None, vertices=32, rotation=(0, 0, 0)):
    bpy.ops.mesh.primitive_cylinder_add(
        vertices=vertices,
        radius=radius,
        depth=depth,
        location=location,
        rotation=rotation,
    )
    obj = bpy.context.object
    obj.name = name
    if material:
        obj.data.materials.append(material)
    try:
        bpy.ops.object.shade_smooth()
    except Exception:
        pass
    return obj


def add_uv_sphere(name, location, radius, material=None, segments=32, rings=16):
    bpy.ops.mesh.primitive_uv_sphere_add(
        segments=segments,
        ring_count=rings,
        radius=radius,
        location=location,
    )
    obj = bpy.context.object
    obj.name = name
    if material:
        obj.data.materials.append(material)
    try:
        bpy.ops.object.shade_smooth()
    except Exception:
        pass
    return obj


def add_text(name, body, location, size, material=None, rotation=(0, 0, 0), align="CENTER"):
    bpy.ops.object.text_add(location=location, rotation=rotation)
    obj = bpy.context.object
    obj.name = name
    obj.data.body = body
    obj.data.align_x = align
    obj.data.align_y = "CENTER"
    obj.data.size = size
    obj.data.extrude = 0.015
    if material:
        obj.data.materials.append(material)
    return obj


def add_empty(name, location):
    empty = bpy.data.objects.new(name, None)
    empty.empty_display_type = "PLAIN_AXES"
    empty.empty_display_size = 1.0
    bpy.context.collection.objects.link(empty)
    empty.location = location
    return empty


def parent_local(obj, parent, location=(0, 0, 0), rotation=None):
    obj.parent = parent
    obj.matrix_parent_inverse.identity()
    obj.location = location
    if rotation is not None:
        obj.rotation_euler = rotation
    return obj


def key_location(obj, frame, location):
    obj.location = location
    obj.keyframe_insert(data_path="location", frame=frame)


def key_rotation(obj, frame, rotation):
    obj.rotation_euler = rotation
    obj.keyframe_insert(data_path="rotation_euler", frame=frame)


def iter_object_fcurves(obj):
    """Yield F-Curves for an object across Blender 4.x and 5.x slotted actions."""
    anim_data = obj.animation_data
    if not anim_data or not anim_data.action:
        return

    action = anim_data.action

    # Blender <= 4.3: action.fcurves directly
    legacy_fcurves = getattr(action, "fcurves", None)
    if legacy_fcurves is not None:
        try:
            if len(legacy_fcurves) > 0:
                yield from legacy_fcurves
                return
        except Exception:
            pass

    # Blender 5.x: slotted actions
    try:
        from bpy_extras import anim_utils
    except ImportError:
        return

    action_slot = getattr(anim_data, "action_slot", None)
    if action_slot is None:
        suitable_slots = getattr(anim_data, "action_suitable_slots", None)
        if suitable_slots:
            action_slot = suitable_slots[0]
    if action_slot is None:
        slots = getattr(action, "slots", None)
        if slots:
            action_slot = slots[0]
    if action_slot is None:
        return

    try:
        channelbag = anim_utils.action_get_channelbag_for_slot(action, action_slot)
        if channelbag is None:
            channelbag = anim_utils.action_ensure_channelbag_for_slot(action, action_slot)
        if channelbag is not None:
            yield from channelbag.fcurves
    except Exception:
        pass


def set_fcurve_interpolation(obj, interpolation="LINEAR"):
    for fcurve in iter_object_fcurves(obj):
        for key in fcurve.keyframe_points:
            try:
                key.interpolation = interpolation
            except (TypeError, ValueError):
                key.interpolation = "BEZIER"


def look_at(obj, target):
    direction = Vector(target) - obj.location
    obj.rotation_euler = direction.to_track_quat("-Z", "Y").to_euler()


def animate_blinking_light(light_obj, start, end, bright=650, interval=10, offset=0):
    for frame in (FRAME_START, start - 1):
        light_obj.data.energy = 0
        light_obj.data.keyframe_insert(data_path="energy", frame=frame)

    frame = start
    toggle = offset
    while frame <= end:
        light_obj.data.energy = bright if toggle % 2 == 0 else 0
        light_obj.data.keyframe_insert(data_path="energy", frame=frame)
        frame += interval
        toggle += 1

    for frame in (end + 1, GATE_UP):
        light_obj.data.energy = 0
        light_obj.data.keyframe_insert(data_path="energy", frame=frame)


def animate_signal_lights(red_light, green_light):
    for frame in (FRAME_START, WARNING_START - 1):
        red_light.data.energy = 0
        green_light.data.energy = 480
        red_light.data.keyframe_insert(data_path="energy", frame=frame)
        green_light.data.keyframe_insert(data_path="energy", frame=frame)

    for frame in (WARNING_START, TRAIN_CLEAR, TRAFFIC_RESUME - 1):
        red_light.data.energy = 620
        green_light.data.energy = 0
        red_light.data.keyframe_insert(data_path="energy", frame=frame)
        green_light.data.keyframe_insert(data_path="energy", frame=frame)

    for frame in (TRAFFIC_RESUME, FRAME_END):
        red_light.data.energy = 0
        green_light.data.energy = 480
        red_light.data.keyframe_insert(data_path="energy", frame=frame)
        green_light.data.keyframe_insert(data_path="energy", frame=frame)


# =====================================================
# MATERIALS  (enhanced saturation / vibrancy)
# =====================================================
grass_mat = make_mat("deep_green_grass", (0.06, 0.42, 0.10, 1), roughness=0.88)
grass_dark_mat = make_mat("grass_variation_dark", (0.03, 0.25, 0.06, 1), roughness=0.93)
road_mat = make_mat("matte_black_asphalt", (0.022, 0.024, 0.026, 1), roughness=0.9)
road_edge_mat = make_mat("asphalt_edge_gray", (0.11, 0.12, 0.12, 1), roughness=0.85)
line_white_mat = make_mat("painted_white_lines", (0.95, 0.95, 0.88, 1), roughness=0.55)
line_yellow_mat = make_mat("painted_yellow_lines", (1.0, 0.82, 0.05, 1), roughness=0.5, emission=(1.0, 0.82, 0.05, 1), emission_strength=0.15)
ballast_mat = make_mat("rail_ballast_stone", (0.32, 0.31, 0.28, 1), roughness=0.95)
wood_mat = make_mat("dark_wood_sleepers", (0.28, 0.15, 0.06, 1), roughness=0.75)
rail_mat = make_mat("brushed_steel_rails", (0.8, 0.82, 0.82, 1), metallic=0.85, roughness=0.2)
red_mat = make_mat("signal_red_emission", (1, 0.02, 0.01, 1), emission=(1, 0, 0, 1), emission_strength=2.5)
green_mat = make_mat("signal_green_emission", (0.04, 1, 0.18, 1), emission=(0.04, 1, 0.18, 1), emission_strength=1.8)
amber_mat = make_mat("amber_glow", (1, 0.55, 0.02, 1), emission=(1, 0.45, 0.01, 1), emission_strength=1.5)
black_mat = make_mat("soft_black", (0.005, 0.005, 0.006, 1), roughness=0.65)
white_mat = make_mat("clean_white", (0.95, 0.95, 0.92, 1), roughness=0.55)
barrier_red_mat = make_mat("barrier_red_stripes", (0.98, 0.03, 0.02, 1), roughness=0.4)
post_mat = make_mat("gate_post_yellow", (1.0, 0.76, 0.04, 1), roughness=0.4)
train_blue_mat = make_mat("train_engine_blue", (0.02, 0.18, 0.92, 1), metallic=0.35, roughness=0.32)
train_yellow_mat = make_mat("train_trim_yellow", (1.0, 0.82, 0.05, 1), metallic=0.1, roughness=0.35)
wagon_red_mat = make_mat("freight_wagon_red", (0.8, 0.04, 0.03, 1), roughness=0.5)
wagon_green_mat = make_mat("freight_wagon_green", (0.02, 0.5, 0.24, 1), roughness=0.55)
wagon_orange_mat = make_mat("freight_wagon_orange", (0.98, 0.35, 0.04, 1), roughness=0.55)
window_mat = make_mat("blue_glass_windows", (0.15, 0.6, 0.82, 1), metallic=0.0, roughness=0.08)
wheel_mat = make_mat("rubber_and_steel_wheels", (0.01, 0.01, 0.01, 1), roughness=0.48)
car_red_mat = make_mat("vehicle_red", (0.88, 0.02, 0.02, 1), metallic=0.15, roughness=0.35)
car_silver_mat = make_mat("vehicle_silver", (0.72, 0.74, 0.76, 1), metallic=0.6, roughness=0.25)
car_teal_mat = make_mat("vehicle_teal", (0.02, 0.6, 0.68, 1), metallic=0.1, roughness=0.38)
car_white_mat = make_mat("vehicle_white", (0.92, 0.92, 0.9, 1), metallic=0.08, roughness=0.32)
truck_blue_mat = make_mat("truck_blue", (0.02, 0.14, 0.78, 1), metallic=0.1, roughness=0.42)
bus_yellow_mat = make_mat("bus_yellow", (1.0, 0.78, 0.02, 1), metallic=0.05, roughness=0.4)
two_wheeler_mat = make_mat("two_wheeler_green", (0.02, 0.72, 0.34, 1), metallic=0.08, roughness=0.38)
delivery_lorry_mat = make_mat("delivery_lorry_orange", (0.95, 0.32, 0.04, 1), metallic=0.08, roughness=0.42)
headlight_mat = make_mat("headlight_glass", (1.0, 0.95, 0.7, 1), emission=(1.0, 0.92, 0.6, 1), emission_strength=2.0)
taillight_mat = make_mat("taillight_red", (1.0, 0.0, 0.0, 1), emission=(1.0, 0.0, 0.0, 1), emission_strength=1.5)
chrome_mat = make_mat("chrome_trim", (0.85, 0.87, 0.88, 1), metallic=0.95, roughness=0.08)
building_mat = make_mat("warm_concrete_buildings", (0.68, 0.62, 0.52, 1), roughness=0.78)
roof_mat = make_mat("dark_roofing", (0.1, 0.08, 0.07, 1), roughness=0.68)
tree_trunk_mat = make_mat("tree_trunk", (0.24, 0.12, 0.04, 1), roughness=0.85)
tree_leaf_mat = make_mat("tree_leaf_cluster", (0.03, 0.38, 0.12, 1), roughness=0.88)
hologram_blue_mat = make_transparent_mat("iot_sensor_hologram_blue", (0.0, 0.65, 1.0), alpha=0.25, emission=(0.0, 0.5, 1.0, 1), emission_strength=0.8)
hologram_green_mat = make_transparent_mat("safe_zone_hologram_green", (0.1, 1.0, 0.42), alpha=0.2, emission=(0.1, 1.0, 0.42, 1), emission_strength=0.5)
hologram_red_mat = make_transparent_mat("danger_zone_hologram_red", (1.0, 0.05, 0.02), alpha=0.18, emission=(1.0, 0.02, 0.0, 1), emission_strength=0.6)
screen_blue_mat = make_mat("control_screen_blue", (0.02, 0.18, 0.28, 1), roughness=0.3, emission=(0.0, 0.65, 1.0, 1), emission_strength=0.6)
score_gold_mat = make_mat("score_gold_emission", (1.0, 0.85, 0.15, 1), roughness=0.25, emission=(1.0, 0.72, 0.05, 1), emission_strength=1.2)
platform_mat = make_mat("station_platform_concrete", (0.74, 0.72, 0.68, 1), roughness=0.72)
station_facade_mat = make_mat("station_cream_facade", (0.9, 0.86, 0.78, 1), roughness=0.68)
toll_booth_mat = make_mat("highway_toll_green", (0.02, 0.48, 0.3, 1), roughness=0.42)
cone_orange_mat = make_mat("safety_cone_orange", (0.98, 0.45, 0.02, 1), roughness=0.45, emission=(0.98, 0.4, 0.02, 1), emission_strength=0.2)
barricade_yellow_mat = make_mat("barricade_reflective_yellow", (1.0, 0.85, 0.05, 1), roughness=0.3)
sign_board_mat = make_mat("road_sign_aluminum", (0.2, 0.22, 0.24, 1), metallic=0.4, roughness=0.35)
tunnel_concrete_mat = make_mat("tunnel_concrete_grey", (0.44, 0.42, 0.4, 1), roughness=0.8)
tunnel_dark_mat = make_mat("tunnel_interior_dark", (0.025, 0.025, 0.03, 1), roughness=0.95)
tunnel_emergency_mat = make_mat("tunnel_emergency_amber", (1.0, 0.5, 0.02, 1), emission=(1.0, 0.4, 0.0, 1), emission_strength=1.0)


# =====================================================
# TERRAIN AND WORLD
# =====================================================
add_cube("large_grass_terrain", (0, 0, -0.035), (180, 120, 0.06), grass_mat)

random.seed(7)
for i in range(52):
    x = random.uniform(-86, 86)
    y = random.uniform(-56, 56)
    if abs(x) < 9 or abs(y) < 7:
        continue
    patch = add_cube(
        f"grass_color_patch_{i:02d}",
        (x, y, -0.01),
        (random.uniform(5, 13), random.uniform(3, 9), 0.012),
        grass_dark_mat,
        rotation=(0, 0, random.uniform(0, math.pi)),
    )
    patch.hide_select = True


# =====================================================
# ROADS AND TRACK BED
# =====================================================
add_cube("main_vertical_road_single_lane", (0, 0, 0.03), (MAIN_ROAD_WIDTH, 104, 0.08), road_mat)
add_cube("upper_service_road", (0, 43, 0.025), (150, 8.0, 0.07), road_edge_mat)
add_cube("lower_service_road", (0, -43, 0.025), (150, 8.0, 0.07), road_edge_mat)
add_cube("rail_ballast_bed", (0, 0, 0.08), (170, 5.2, 0.26), ballast_mat)

for y in (-1.15, 1.15):
    add_cube(f"continuous_steel_rail_y_{y}", (0, y, 0.35), (168, 0.18, 0.28), rail_mat)

for x in range(-82, 83, 3):
    add_cube(f"wooden_sleeper_{x}", (x, 0, 0.18), (0.42, 3.75, 0.18), wood_mat)

for x in (-MAIN_ROAD_WIDTH * 0.5, MAIN_ROAD_WIDTH * 0.5):
    add_cube(f"single_lane_edge_line_{x}", (x, 0, 0.108), (0.14, 97, 0.018), line_white_mat)

add_cube("north_stop_bar", (0, 14.0, 0.12), (MAIN_ROAD_WIDTH - 0.75, 0.36, 0.025), line_white_mat)
add_cube("south_stop_bar", (0, -14.0, 0.12), (MAIN_ROAD_WIDTH - 0.75, 0.36, 0.025), line_white_mat)

add_text("north_road_marking", "ONE LANE\nRAIL XING", (0, 24, 0.14), 1.0, line_white_mat, rotation=(0, 0, 0))
add_text("south_road_marking", "ONE LANE\nRAIL XING", (0, -24, 0.14), 1.0, line_white_mat, rotation=(0, 0, math.pi))


# =====================================================
# TRAIN
# =====================================================
train_root = add_empty("TRAIN_ROOT_ANIMATED", (-135, 0, 0))

engine_body = add_cube("engine_main_body", (0, 0, 0), (7.8, 2.6, 1.85), train_blue_mat)
parent_local(engine_body, train_root, (0, 0, 1.25))
engine_cab = add_cube("engine_driver_cab", (0, 0, 0), (2.55, 2.45, 1.55), train_blue_mat)
parent_local(engine_cab, train_root, (-2.5, 0, 2.25))
engine_nose = add_cube("engine_front_nose", (0, 0, 0), (2.0, 2.25, 1.15), train_yellow_mat)
parent_local(engine_nose, train_root, (4.7, 0, 1.35))
engine_window = add_cube("engine_front_window", (0, 0, 0), (0.08, 1.7, 0.55), window_mat)
parent_local(engine_window, train_root, (-1.1, 0, 2.45))
chimney = add_cylinder("engine_chimney", (0, 0, 0), 0.28, 0.9, black_mat, vertices=32)
parent_local(chimney, train_root, (2.2, 0, 2.55), rotation=(0, 0, 0))
headlamp = add_uv_sphere("engine_headlamp_glass", (0, 0, 0), 0.25, headlight_mat)
parent_local(headlamp, train_root, (5.78, 0, 1.55))

# Engine details — cowcatcher / pilot
cowcatcher = add_cube("engine_cowcatcher", (0, 0, 0), (0.6, 2.8, 0.4), chrome_mat)
parent_local(cowcatcher, train_root, (5.9, 0, 0.55))

# Horn on top
horn = add_cylinder("engine_horn", (0, 0, 0), 0.08, 0.55, chrome_mat, vertices=12, rotation=(0, math.radians(45), 0))
parent_local(horn, train_root, (1.5, 0.4, 2.65))

bpy.ops.object.light_add(type="POINT", location=(0, 0, 0))
headlamp_light = bpy.context.object
headlamp_light.name = "engine_warm_headlight"
headlamp_light.data.color = (1.0, 0.74, 0.25)
headlamp_light.data.energy = 850
headlamp_light.data.shadow_soft_size = 6
parent_local(headlamp_light, train_root, (6.2, 0, 1.55))

wagon_mats = [wagon_red_mat, wagon_green_mat, wagon_orange_mat, wagon_red_mat]
for index, x in enumerate([-8.5, -16.2, -23.9, -31.6]):
    wagon = add_cube(f"freight_wagon_{index + 1}", (0, 0, 0), (6.8, 2.55, 1.55), wagon_mats[index])
    parent_local(wagon, train_root, (x, 0, 1.15))
    top_lip = add_cube(f"freight_wagon_{index + 1}_top_lip", (0, 0, 0), (6.9, 2.7, 0.16), black_mat)
    parent_local(top_lip, train_root, (x, 0, 1.98))
    coupler = add_cube(f"freight_wagon_{index + 1}_coupler", (0, 0, 0), (0.55, 0.45, 0.25), black_mat)
    parent_local(coupler, train_root, (x + 3.65, 0, 0.76))

wheel_x_positions = [3.0, 0.2, -3.1, -6.9, -10.2, -14.6, -17.9, -22.3, -25.6, -30.0, -33.3]
for i, x in enumerate(wheel_x_positions):
    for y in (-1.45, 1.45):
        wheel = add_cylinder(
            f"train_wheel_{i}_{y}",
            (0, 0, 0),
            0.42,
            0.22,
            wheel_mat,
            vertices=32,
            rotation=(math.radians(90), 0, 0),
        )
        parent_local(wheel, train_root, (x, y, 0.45), rotation=(math.radians(90), 0, 0))

key_location(train_root, FRAME_START, (-135, 0, 0))
key_location(train_root, WARNING_START, (-68, 0, 0))
key_location(train_root, GATE_DOWN, (-28, 0, 0))
key_location(train_root, 180, (6, 0, 0))
key_location(train_root, TRAIN_CLEAR, (75, 0, 0))
key_location(train_root, FRAME_END, (145, 0, 0))
set_fcurve_interpolation(train_root, "LINEAR")


# =====================================================
# GATES, WARNING LIGHTS, AND SIGNALS
# =====================================================
def create_gate(name, pivot_x, pivot_y, direction, open_angle_deg):
    post = add_cylinder(f"{name}_post", (pivot_x, pivot_y, 1.25), 0.18, 2.5, post_mat, vertices=24)
    base = add_cube(f"{name}_concrete_base", (pivot_x, pivot_y, 0.18), (1.0, 1.0, 0.36), ballast_mat)
    box = add_cube(f"{name}_motor_box", (pivot_x, pivot_y, 2.45), (0.78, 0.52, 0.42), black_mat)

    arm_pivot = add_empty(f"{name}_arm_pivot", (pivot_x, pivot_y, 2.2))
    arm_length = MAIN_ROAD_WIDTH + 1.6

    main_arm = add_cube(f"{name}_white_gate_arm", (0, 0, 0), (arm_length, 0.18, 0.18), white_mat)
    parent_local(main_arm, arm_pivot, (direction * arm_length * 0.5, 0, 0))

    for i, local_x in enumerate([1.15, 2.55, 3.95, 5.35, 6.75]):
        stripe = add_cube(f"{name}_red_stripe_{i}", (0, 0, 0), (0.58, 0.22, 0.22), barrier_red_mat)
        parent_local(stripe, arm_pivot, (direction * local_x, 0, 0))

    for i, local_x in enumerate([2.0, 4.5, 7.0]):
        lamp = add_uv_sphere(f"{name}_arm_red_marker_{i}", (0, 0, 0), 0.14, red_mat)
        parent_local(lamp, arm_pivot, (direction * local_x, -0.12, 0.16))

    light_a = add_uv_sphere(f"{name}_warning_lamp_left", (pivot_x - 0.22, pivot_y, 2.9), 0.18, red_mat)
    light_b = add_uv_sphere(f"{name}_warning_lamp_right", (pivot_x + 0.22, pivot_y, 2.9), 0.18, red_mat)

    bpy.ops.object.light_add(type="POINT", location=light_a.location)
    blink_a = bpy.context.object
    blink_a.name = f"{name}_blink_light_a"
    blink_a.data.color = (1, 0, 0)
    blink_a.data.shadow_soft_size = 3
    bpy.ops.object.light_add(type="POINT", location=light_b.location)
    blink_b = bpy.context.object
    blink_b.name = f"{name}_blink_light_b"
    blink_b.data.color = (1, 0, 0)
    blink_b.data.shadow_soft_size = 3

    open_rotation = (0, math.radians(open_angle_deg), 0)
    closed_rotation = (0, 0, 0)
    key_rotation(arm_pivot, FRAME_START, open_rotation)
    key_rotation(arm_pivot, WARNING_START, open_rotation)
    key_rotation(arm_pivot, GATE_DOWN, closed_rotation)
    key_rotation(arm_pivot, TRAIN_CLEAR, closed_rotation)
    key_rotation(arm_pivot, GATE_UP, open_rotation)
    key_rotation(arm_pivot, FRAME_END, open_rotation)
    set_fcurve_interpolation(arm_pivot, "BEZIER")

    animate_blinking_light(blink_a, WARNING_START, TRAIN_CLEAR, bright=860, interval=10, offset=0)
    animate_blinking_light(blink_b, WARNING_START, TRAIN_CLEAR, bright=860, interval=10, offset=1)

    return {
        "post": post,
        "base": base,
        "box": box,
        "pivot": arm_pivot,
        "warning_lamps": (light_a, light_b),
    }


north_gate = create_gate("north_gate", -4.25, 10.2, 1, -72)
south_gate = create_gate("south_gate", 4.25, -10.2, -1, 72)

for side_name, y, text_rot in (("north", 16.2, 0), ("south", -16.2, math.pi)):
    pole = add_cylinder(f"{side_name}_traffic_signal_pole", (-4.15, y, 1.45), 0.08, 2.9, black_mat, vertices=16)
    housing = add_cube(f"{side_name}_traffic_signal_housing", (-4.15, y, 2.85), (0.55, 0.35, 0.95), black_mat)
    red_ball = add_uv_sphere(f"{side_name}_traffic_red_lens", (-4.15, y - 0.03, 3.08), 0.16, red_mat)
    green_ball = add_uv_sphere(f"{side_name}_traffic_green_lens", (-4.15, y - 0.03, 2.62), 0.16, green_mat)
    bpy.ops.object.light_add(type="POINT", location=red_ball.location)
    red_light = bpy.context.object
    red_light.name = f"{side_name}_traffic_red_light"
    red_light.data.color = (1, 0, 0)
    red_light.data.shadow_soft_size = 4
    bpy.ops.object.light_add(type="POINT", location=green_ball.location)
    green_light = bpy.context.object
    green_light.name = f"{side_name}_traffic_green_light"
    green_light.data.color = (0.0, 1.0, 0.18)
    green_light.data.shadow_soft_size = 4
    animate_signal_lights(red_light, green_light)
    add_text(
        f"{side_name}_stop_sign_text",
        "STOP",
        (-4.9, y, 2.75),
        0.42,
        white_mat,
        rotation=(math.radians(90), 0, text_rot),
    )

add_cube("crossing_control_booth", (9.6, 11.8, 1.05), (2.6, 2.1, 2.1), building_mat)
add_cube("crossing_control_booth_roof", (9.6, 11.8, 2.3), (3.0, 2.5, 0.28), roof_mat)
add_cube("control_booth_window", (8.28, 11.8, 1.45), (0.06, 1.2, 0.65), window_mat)
add_text("warning_sign_text", "TRAIN\nAPPROACHING", (9.55, 10.55, 2.0), 0.36, amber_mat, rotation=(math.radians(90), 0, 0))


# =====================================================
# GAMEFUL IOT CONTROL LAYER (TOLL PLAZA SIDE)
# =====================================================
toll_console_x = TOLL_BOOTH_X + 1.5
toll_console_y = TOLL_ROAD_Y + 8.8
add_cube("iot_control_console_body", (toll_console_x, toll_console_y, 1.75), (0.75, 4.8, 3.2), black_mat)
add_cube("iot_control_console_screen", (toll_console_x - 0.38, toll_console_y, 2.05), (0.08, 4.2, 2.45), screen_blue_mat)
add_text(
    "iot_panel_title",
    "SMART TOLL CONTROL",
    (toll_console_x - 0.45, toll_console_y + 1.55, 2.95),
    0.22,
    score_gold_mat,
    rotation=(math.radians(90), 0, math.pi),
    align="LEFT",
)
iot_status_text = add_text(
    "iot_live_status_text",
    "LINK: SIM MODE\nTOLL: OPEN\nLANE: CLEAR\nQUEUE: 0\nRISK: LOW",
    (toll_console_x - 0.45, toll_console_y + 0.95, 2.4),
    0.22,
    white_mat,
    rotation=(math.radians(90), 0, math.pi),
    align="LEFT",
)
iot_distance_text = add_text(
    "iot_live_distance_text",
    "TRAIN DISTANCE\n-- m",
    (toll_console_x - 0.45, toll_console_y - 0.85, 2.45),
    0.3,
    hologram_blue_mat,
    rotation=(math.radians(90), 0, math.pi),
    align="CENTER",
)
add_text(
    "game_score_text",
    "SAFETY SCORE 100",
    (toll_console_x - 0.45, toll_console_y - 2.1, 1.1),
    0.24,
    score_gold_mat,
    rotation=(math.radians(90), 0, math.pi),
    align="LEFT",
)

for side, x in (("west", -38), ("east", 38)):
    add_cube(f"{side}_approach_sensor_zone", (x, 0, 0.24), (17.0, 4.9, 0.06), hologram_blue_mat)
    add_torus(
        f"{side}_lidar_sensor_ring",
        (x, 0, 2.65),
        2.7,
        0.035,
        hologram_blue_mat,
        rotation=(0, math.radians(90), 0),
    )
    add_cylinder(f"{side}_sensor_pole_left", (x, -3.0, 1.45), 0.06, 2.9, black_mat, vertices=14)
    add_cylinder(f"{side}_sensor_pole_right", (x, 3.0, 1.45), 0.06, 2.9, black_mat, vertices=14)
    add_cube(f"{side}_sensor_bridge", (x, 0, 2.9), (0.18, 6.2, 0.16), black_mat)
    bpy.ops.object.light_add(type="POINT", location=(x, 0, 2.9))
    sensor_light = bpy.context.object
    sensor_light.name = f"{side}_iot_sensor_beacon"
    sensor_light.data.color = (0.0, 0.55, 1.0)
    sensor_light.data.energy = 280
    sensor_light.data.shadow_soft_size = 5

crossing_danger_zone = add_cube("red_locked_crossing_zone_when_train_passes", (0, 0, 0.27), (13.2, 6.0, 0.045), hologram_red_mat)
crossing_danger_zone.hide_viewport = False

iot_tower = add_cylinder("thingsboard_gateway_tower", (TOLL_BOOTH_X + 8.5, TOLL_ROAD_Y + 9.2, 2.2), 0.12, 4.4, black_mat, vertices=18)
iot_tower_top = add_uv_sphere("thingsboard_gateway_antenna", (TOLL_BOOTH_X + 8.5, TOLL_ROAD_Y + 9.2, 4.55), 0.34, hologram_blue_mat)
for i, radius in enumerate([0.85, 1.35, 1.85]):
    ring = add_torus(
        f"thingsboard_signal_wave_{i + 1}",
        (TOLL_BOOTH_X + 8.5, TOLL_ROAD_Y + 9.2, 4.55),
        radius,
        0.025,
        hologram_blue_mat,
        rotation=(math.radians(90), 0, 0),
    )
    ring.keyframe_insert(data_path="scale", frame=FRAME_START + i * 8)
    ring.scale = (1.25, 1.25, 1.25)
    ring.keyframe_insert(data_path="scale", frame=FRAME_START + 45 + i * 8)
    ring.scale = (1.0, 1.0, 1.0)
    ring.keyframe_insert(data_path="scale", frame=FRAME_START + 90 + i * 8)
    set_fcurve_interpolation(ring, "SINE")

bpy.ops.object.light_add(type="POINT", location=(TOLL_BOOTH_X + 8.5, TOLL_ROAD_Y + 9.2, 4.55))
iot_tower_light = bpy.context.object
iot_tower_light.name = "thingsboard_gateway_blue_beacon"
iot_tower_light.data.color = (0.0, 0.5, 1.0)
iot_tower_light.data.energy = 580
iot_tower_light.data.shadow_soft_size = 8

toll_vehicle_detect_text = None
tunnel_emergency_text = None


# =====================================================
# REALISTIC VEHICLE BUILDER
# =====================================================
def create_realistic_car(name, material, root_obj, local_offset=(0, 0, 0)):
    """Build a detailed sedan-shaped car parented to root_obj."""
    ox, oy, oz = local_offset

    # Underbody / chassis
    chassis = add_cube(f"{name}_chassis", (0, 0, 0), (4.2, 1.85, 0.18), black_mat)
    parent_local(chassis, root_obj, (ox, oy, oz + 0.32))

    # Lower body
    body = add_cube(f"{name}_body", (0, 0, 0), (4.0, 1.82, 0.72), material)
    parent_local(body, root_obj, (ox, oy, oz + 0.72))

    # Hood (front, sloped)
    hood = add_cube(f"{name}_hood", (0, 0, 0), (1.15, 1.78, 0.12), material)
    parent_local(hood, root_obj, (ox + 1.2, oy, oz + 1.02), rotation=(0, math.radians(-5), 0))

    # Trunk (rear)
    trunk = add_cube(f"{name}_trunk", (0, 0, 0), (0.85, 1.78, 0.12), material)
    parent_local(trunk, root_obj, (ox - 1.4, oy, oz + 1.0), rotation=(0, math.radians(3), 0))

    # Cabin / greenhouse
    cabin = add_cube(f"{name}_cabin", (0, 0, 0), (1.85, 1.68, 0.62), material)
    parent_local(cabin, root_obj, (ox - 0.15, oy, oz + 1.42))

    # Windshield (front, angled)
    windshield = add_cube(f"{name}_windshield", (0, 0, 0), (0.05, 1.52, 0.52), window_mat)
    parent_local(windshield, root_obj, (ox + 0.78, oy, oz + 1.38), rotation=(0, math.radians(25), 0))

    # Rear window
    rear_window = add_cube(f"{name}_rear_window", (0, 0, 0), (0.05, 1.48, 0.46), window_mat)
    parent_local(rear_window, root_obj, (ox - 1.05, oy, oz + 1.36), rotation=(0, math.radians(-22), 0))

    # Side windows
    for side_y, side_label in ((0.92, "left"), (-0.92, "right")):
        side_win = add_cube(f"{name}_{side_label}_window", (0, 0, 0), (1.55, 0.05, 0.38), window_mat)
        parent_local(side_win, root_obj, (ox - 0.1, oy + side_y, oz + 1.42))

    # Headlights
    for hl_y in (-0.65, 0.65):
        hl = add_uv_sphere(f"{name}_headlight_{hl_y}", (0, 0, 0), 0.12, headlight_mat, segments=16, rings=8)
        parent_local(hl, root_obj, (ox + 2.05, oy + hl_y, oz + 0.72))

    # Taillights
    for tl_y in (-0.72, 0.72):
        tl = add_cube(f"{name}_taillight_{tl_y}", (0, 0, 0), (0.06, 0.28, 0.15), taillight_mat)
        parent_local(tl, root_obj, (ox - 2.02, oy + tl_y, oz + 0.78))

    # Bumpers
    front_bumper = add_cube(f"{name}_front_bumper", (0, 0, 0), (0.18, 1.9, 0.28), chrome_mat)
    parent_local(front_bumper, root_obj, (ox + 2.08, oy, oz + 0.48))
    rear_bumper = add_cube(f"{name}_rear_bumper", (0, 0, 0), (0.18, 1.9, 0.28), chrome_mat)
    parent_local(rear_bumper, root_obj, (ox - 2.08, oy, oz + 0.48))

    # Side mirrors
    for my in (-0.98, 0.98):
        mirror = add_cube(f"{name}_mirror_{my}", (0, 0, 0), (0.18, 0.12, 0.08), black_mat)
        parent_local(mirror, root_obj, (ox + 0.55, oy + my, oz + 1.22))

    # Wheels
    wheel_positions = [
        (ox + 1.25, oy - 0.98, oz + 0.34),
        (ox + 1.25, oy + 0.98, oz + 0.34),
        (ox - 1.25, oy - 0.98, oz + 0.34),
        (ox - 1.25, oy + 0.98, oz + 0.34),
    ]
    wheels = []
    for idx, wpos in enumerate(wheel_positions):
        w = add_cylinder(f"{name}_wheel_{idx}", (0, 0, 0), 0.32, 0.2, wheel_mat, vertices=28, rotation=(math.radians(90), 0, 0))
        parent_local(w, root_obj, wpos, rotation=(math.radians(90), 0, 0))
        # Hub cap
        hub = add_cylinder(f"{name}_hubcap_{idx}", (0, 0, 0), 0.18, 0.05, chrome_mat, vertices=16, rotation=(math.radians(90), 0, 0))
        parent_local(hub, root_obj, (wpos[0], wpos[1] + (0.12 if wpos[1] > 0 else -0.12), wpos[2]), rotation=(math.radians(90), 0, 0))
        wheels.append(w)

    return wheels


def create_realistic_truck(name, material, root_obj, local_offset=(0, 0, 0)):
    """Build a detailed truck / pickup parented to root_obj."""
    ox, oy, oz = local_offset

    # Chassis
    chassis = add_cube(f"{name}_chassis", (0, 0, 0), (6.0, 2.1, 0.2), black_mat)
    parent_local(chassis, root_obj, (ox, oy, oz + 0.42))

    # Cab
    cab = add_cube(f"{name}_cab", (0, 0, 0), (2.4, 2.08, 1.45), material)
    parent_local(cab, root_obj, (ox + 1.5, oy, oz + 1.18))

    # Cab roof
    cab_roof = add_cube(f"{name}_cab_roof", (0, 0, 0), (2.2, 2.12, 0.12), material)
    parent_local(cab_roof, root_obj, (ox + 1.5, oy, oz + 1.95))

    # Cargo bed
    cargo = add_cube(f"{name}_cargo_bed", (0, 0, 0), (3.4, 2.08, 1.8), material)
    parent_local(cargo, root_obj, (ox - 1.45, oy, oz + 1.4))

    # Cargo top lip
    lip = add_cube(f"{name}_cargo_lip", (0, 0, 0), (3.5, 2.15, 0.12), black_mat)
    parent_local(lip, root_obj, (ox - 1.45, oy, oz + 2.35))

    # Windshield
    ws = add_cube(f"{name}_windshield", (0, 0, 0), (0.05, 1.82, 0.65), window_mat)
    parent_local(ws, root_obj, (ox + 2.72, oy, oz + 1.52), rotation=(0, math.radians(12), 0))

    # Side windows
    for sy, sl in ((1.06, "left"), (-1.06, "right")):
        sw = add_cube(f"{name}_{sl}_window", (0, 0, 0), (1.6, 0.05, 0.5), window_mat)
        parent_local(sw, root_obj, (ox + 1.5, oy + sy, oz + 1.55))

    # Headlights
    for hl_y in (-0.72, 0.72):
        hl = add_uv_sphere(f"{name}_headlight_{hl_y}", (0, 0, 0), 0.14, headlight_mat, segments=14, rings=8)
        parent_local(hl, root_obj, (ox + 2.75, oy + hl_y, oz + 0.85))

    # Taillights
    for tl_y in (-0.82, 0.82):
        tl = add_cube(f"{name}_taillight_{tl_y}", (0, 0, 0), (0.06, 0.32, 0.18), taillight_mat)
        parent_local(tl, root_obj, (ox - 3.02, oy + tl_y, oz + 0.85))

    # Bumpers
    add_cube(f"{name}_front_bumper", (0, 0, 0), (0.22, 2.15, 0.38), chrome_mat)
    parent_local(bpy.context.object, root_obj, (ox + 2.92, oy, oz + 0.55))
    add_cube(f"{name}_rear_bumper", (0, 0, 0), (0.2, 2.15, 0.32), chrome_mat)
    parent_local(bpy.context.object, root_obj, (ox - 3.08, oy, oz + 0.52))

    # Exhaust pipe
    exhaust = add_cylinder(f"{name}_exhaust", (0, 0, 0), 0.05, 0.6, chrome_mat, vertices=12, rotation=(math.radians(90), 0, 0))
    parent_local(exhaust, root_obj, (ox - 2.8, oy - 0.85, oz + 0.42))

    # Wheels (larger)
    wheel_positions = [
        (ox + 1.65, oy - 1.12, oz + 0.42),
        (ox + 1.65, oy + 1.12, oz + 0.42),
        (ox - 1.85, oy - 1.12, oz + 0.42),
        (ox - 1.85, oy + 1.12, oz + 0.42),
    ]
    wheels = []
    for idx, wpos in enumerate(wheel_positions):
        w = add_cylinder(f"{name}_wheel_{idx}", (0, 0, 0), 0.4, 0.24, wheel_mat, vertices=28, rotation=(math.radians(90), 0, 0))
        parent_local(w, root_obj, wpos, rotation=(math.radians(90), 0, 0))
        wheels.append(w)

    return wheels


def create_realistic_bus(name, material, root_obj, local_offset=(0, 0, 0)):
    """Build a detailed city bus parented to root_obj."""
    ox, oy, oz = local_offset

    # Main body
    body = add_cube(f"{name}_body", (0, 0, 0), (7.6, 2.2, 1.85), material)
    parent_local(body, root_obj, (ox, oy, oz + 1.25))

    # Roof
    roof = add_cube(f"{name}_roof", (0, 0, 0), (7.4, 2.15, 0.12), black_mat)
    parent_local(roof, root_obj, (ox, oy, oz + 2.22))

    # Windshield
    ws = add_cube(f"{name}_windshield", (0, 0, 0), (0.06, 1.85, 0.95), window_mat)
    parent_local(ws, root_obj, (ox + 3.82, oy, oz + 1.55), rotation=(0, math.radians(8), 0))

    # Rear window
    rw = add_cube(f"{name}_rear_window", (0, 0, 0), (0.06, 1.62, 0.65), window_mat)
    parent_local(rw, root_obj, (ox - 3.82, oy, oz + 1.62))

    # Side windows (multiple)
    for side_y, sl in ((1.12, "left"), (-1.12, "right")):
        for i, win_x in enumerate([-2.6, -1.5, -0.4, 0.7, 1.8, 2.9]):
            sw = add_cube(f"{name}_{sl}_window_{i}", (0, 0, 0), (0.85, 0.05, 0.62), window_mat)
            parent_local(sw, root_obj, (ox + win_x, oy + side_y, oz + 1.52))

    # Door indication (front)
    door = add_cube(f"{name}_front_door", (0, 0, 0), (0.9, 0.06, 1.3), black_mat)
    parent_local(door, root_obj, (ox + 2.8, oy + 1.12, oz + 1.0))

    # Route sign
    sign = add_cube(f"{name}_route_sign", (0, 0, 0), (1.8, 0.06, 0.42), screen_blue_mat)
    parent_local(sign, root_obj, (ox + 1.5, oy + 1.12, oz + 2.05))

    # Headlights
    for hl_y in (-0.78, 0.78):
        hl = add_uv_sphere(f"{name}_headlight_{hl_y}", (0, 0, 0), 0.14, headlight_mat, segments=14, rings=8)
        parent_local(hl, root_obj, (ox + 3.85, oy + hl_y, oz + 0.82))

    # Taillights
    for tl_y in (-0.85, 0.85):
        tl = add_cube(f"{name}_taillight_{tl_y}", (0, 0, 0), (0.06, 0.35, 0.2), taillight_mat)
        parent_local(tl, root_obj, (ox - 3.85, oy + tl_y, oz + 0.85))

    # Bumpers
    fb = add_cube(f"{name}_front_bumper", (0, 0, 0), (0.22, 2.25, 0.35), chrome_mat)
    parent_local(fb, root_obj, (ox + 3.92, oy, oz + 0.52))
    rb = add_cube(f"{name}_rear_bumper", (0, 0, 0), (0.2, 2.25, 0.3), chrome_mat)
    parent_local(rb, root_obj, (ox - 3.92, oy, oz + 0.5))

    # Mirrors
    for my in (-1.25, 1.25):
        mirror_arm = add_cube(f"{name}_mirror_arm_{my}", (0, 0, 0), (0.05, 0.3, 0.05), black_mat)
        parent_local(mirror_arm, root_obj, (ox + 3.5, oy + my, oz + 1.75))
        mirror_glass = add_cube(f"{name}_mirror_glass_{my}", (0, 0, 0), (0.15, 0.08, 0.2), chrome_mat)
        parent_local(mirror_glass, root_obj, (ox + 3.5, oy + my + (0.18 if my > 0 else -0.18), oz + 1.72))

    # Wheels
    wheel_positions = [
        (ox + 2.5, oy - 1.18, oz + 0.38),
        (ox + 2.5, oy + 1.18, oz + 0.38),
        (ox - 2.5, oy - 1.18, oz + 0.38),
        (ox - 2.5, oy + 1.18, oz + 0.38),
    ]
    wheels = []
    for idx, wpos in enumerate(wheel_positions):
        w = add_cylinder(f"{name}_wheel_{idx}", (0, 0, 0), 0.38, 0.22, wheel_mat, vertices=28, rotation=(math.radians(90), 0, 0))
        parent_local(w, root_obj, wpos, rotation=(math.radians(90), 0, 0))
        wheels.append(w)

    return wheels


def create_two_wheeler(name, material, root_obj, local_offset=(0, 0, 0)):
    """Build a compact motorcycle / scooter model parented to root_obj."""
    ox, oy, oz = local_offset

    frame = add_cube(f"{name}_main_frame", (0, 0, 0), (1.55, 0.18, 0.18), black_mat)
    parent_local(frame, root_obj, (ox, oy, oz + 0.75), rotation=(0, math.radians(-8), 0))

    front_fork = add_cylinder(
        f"{name}_front_fork",
        (0, 0, 0),
        0.04,
        1.0,
        chrome_mat,
        vertices=12,
        rotation=(0, math.radians(22), 0),
    )
    parent_local(front_fork, root_obj, (ox + 0.88, oy, oz + 0.85), rotation=(0, math.radians(22), 0))

    rear_support = add_cylinder(
        f"{name}_rear_support",
        (0, 0, 0),
        0.04,
        0.8,
        chrome_mat,
        vertices=12,
        rotation=(0, math.radians(-28), 0),
    )
    parent_local(rear_support, root_obj, (ox - 0.65, oy, oz + 0.8), rotation=(0, math.radians(-28), 0))

    fuel_tank = add_cube(f"{name}_fuel_tank", (0, 0, 0), (0.95, 0.46, 0.34), material)
    parent_local(fuel_tank, root_obj, (ox + 0.05, oy, oz + 1.05), rotation=(0, math.radians(-4), 0))

    seat = add_cube(f"{name}_seat", (0, 0, 0), (0.95, 0.42, 0.14), black_mat)
    parent_local(seat, root_obj, (ox - 0.55, oy, oz + 1.17), rotation=(0, math.radians(4), 0))

    handlebar = add_cube(f"{name}_handlebar", (0, 0, 0), (0.18, 1.0, 0.08), chrome_mat)
    parent_local(handlebar, root_obj, (ox + 1.05, oy, oz + 1.28), rotation=(0, 0, 0))

    headlamp = add_uv_sphere(f"{name}_headlamp", (0, 0, 0), 0.12, headlight_mat, segments=16, rings=8)
    parent_local(headlamp, root_obj, (ox + 1.08, oy, oz + 1.03))

    tail_lamp = add_cube(f"{name}_tail_lamp", (0, 0, 0), (0.06, 0.24, 0.12), taillight_mat)
    parent_local(tail_lamp, root_obj, (ox - 1.05, oy, oz + 0.98))

    rider_body = add_cube(f"{name}_rider_body", (0, 0, 0), (0.34, 0.38, 0.75), white_mat)
    parent_local(rider_body, root_obj, (ox - 0.25, oy, oz + 1.75), rotation=(0, math.radians(8), 0))
    rider_helmet = add_uv_sphere(f"{name}_rider_helmet", (0, 0, 0), 0.23, material, segments=16, rings=8)
    parent_local(rider_helmet, root_obj, (ox - 0.18, oy, oz + 2.24))

    wheel_positions = [
        (ox + 0.98, oy, oz + 0.38),
        (ox - 0.98, oy, oz + 0.38),
    ]
    wheels = []
    for idx, wpos in enumerate(wheel_positions):
        wheel = add_cylinder(
            f"{name}_wheel_{idx}",
            (0, 0, 0),
            0.38,
            0.12,
            wheel_mat,
            vertices=28,
            rotation=(math.radians(90), 0, 0),
        )
        parent_local(wheel, root_obj, wpos, rotation=(math.radians(90), 0, 0))
        hub = add_cylinder(
            f"{name}_wheel_hub_{idx}",
            (0, 0, 0),
            0.15,
            0.14,
            chrome_mat,
            vertices=16,
            rotation=(math.radians(90), 0, 0),
        )
        parent_local(hub, root_obj, wpos, rotation=(math.radians(90), 0, 0))
        wheels.append(wheel)

    return wheels


# =====================================================
# MAIN ROAD VEHICLES (north-south, stop at crossing)
# =====================================================
def create_main_road_vehicle(name, material, lane_x, start_y, stop_y, end_y, direction, vehicle_type="car"):
    """
    Animate a vehicle along the main north-south road.
    direction: +1 = northbound, -1 = southbound
    Vehicles approach, stop at stop bar during gate closure, then resume.
    """
    root = add_empty(f"{name}_ROOT", (lane_x, start_y, 0))

    # Build vehicle geometry (rotated 90° for north-south travel)
    rot_z = math.radians(90) if direction > 0 else math.radians(-90)
    root.rotation_euler = (0, 0, rot_z)

    if vehicle_type == "car":
        wheels = create_realistic_car(name, material, root)
    elif vehicle_type == "truck":
        wheels = create_realistic_truck(name, material, root)
    elif vehicle_type == "two_wheeler":
        wheels = create_two_wheeler(name, material, root)
    else:
        wheels = create_realistic_bus(name, material, root)

    # Reset rotation for animation (we bake the visual rotation, animate Y)
    # Actually, keep the rotation and animate location
    root.rotation_euler = (0, 0, rot_z)
    root.keyframe_insert(data_path="rotation_euler", frame=FRAME_START)

    # Animation keyframes
    # Phase 1: approach (frame 1 to WARNING_START)
    # Phase 2: slow down and stop (WARNING_START to GATE_DOWN)
    # Phase 3: wait (GATE_DOWN to TRAFFIC_RESUME)
    # Phase 4: resume and drive away (TRAFFIC_RESUME to FRAME_END)

    approach_y = start_y + direction * 18
    key_location(root, FRAME_START, (lane_x, start_y, 0))
    key_location(root, WARNING_START - 10, (lane_x, approach_y, 0))
    key_location(root, GATE_DOWN - 5, (lane_x, stop_y, 0))
    key_location(root, TRAFFIC_RESUME, (lane_x, stop_y, 0))
    key_location(root, TRAFFIC_RESUME + 15, (lane_x, stop_y + direction * 6, 0))
    key_location(root, FRAME_END, (lane_x, end_y, 0))
    set_fcurve_interpolation(root, "LINEAR")

    # Animate wheel rotation
    for w in wheels:
        w.rotation_euler = (0, 0, 0)
        w.keyframe_insert(data_path="rotation_euler", frame=FRAME_START)
        w.rotation_euler = (0, 0, math.radians(720))
        w.keyframe_insert(data_path="rotation_euler", frame=GATE_DOWN - 5)
        w.rotation_euler = (0, 0, math.radians(720))
        w.keyframe_insert(data_path="rotation_euler", frame=TRAFFIC_RESUME)
        w.rotation_euler = (0, 0, math.radians(1800))
        w.keyframe_insert(data_path="rotation_euler", frame=FRAME_END)
        set_fcurve_interpolation(w, "LINEAR")

    return root


VEHICLE_SELECTOR_ITEMS = [
    ("car", "Car", "Simulate a car on the single lane"),
    ("bus", "Bus", "Simulate a bus on the single lane"),
    ("two_wheeler", "Two Wheeler", "Simulate a two wheeler on the single lane"),
]
VEHICLE_LABELS = {
    "car": "Car",
    "bus": "Bus",
    "two_wheeler": "Two Wheeler",
}
vehicle_selector_status_text = None


def normalize_vehicle_type(vehicle_type):
    if vehicle_type in VEHICLE_LABELS:
        return vehicle_type
    return "car"


def remove_objects_by_prefix(prefix):
    for obj in list(bpy.data.objects):
        if obj.name.startswith(prefix):
            bpy.data.objects.remove(obj, do_unlink=True)


def update_vehicle_selector_status(vehicle_type):
    status_obj = bpy.data.objects.get("vehicle_selector_status_text")
    if status_obj and status_obj.data:
        status_obj.data.body = f"ACTIVE\n{VEHICLE_LABELS[vehicle_type].upper()}"


def create_vehicle_selector_panel(active_vehicle_type="car"):
    """Create visible selector signage in the scene; the real buttons live in the Blender sidebar."""
    global vehicle_selector_status_text

    panel_x = -11.5
    panel_y = -34.5
    panel_z = 2.05
    active_vehicle_type = normalize_vehicle_type(active_vehicle_type)

    add_cube("vehicle_selector_panel_body", (panel_x, panel_y, panel_z), (0.14, 6.2, 3.35), black_mat)
    add_cube("vehicle_selector_panel_screen", (panel_x - 0.08, panel_y, panel_z + 0.15), (0.08, 5.7, 2.85), screen_blue_mat)
    add_text(
        "vehicle_selector_title_text",
        "SIM VEHICLE\nSELECTOR",
        (panel_x - 0.17, panel_y + 1.85, panel_z + 1.0),
        0.22,
        score_gold_mat,
        rotation=(math.radians(90), 0, math.pi),
        align="CENTER",
    )

    button_specs = [
        ("car", "CAR", car_red_mat, 0.55),
        ("bus", "BUS", bus_yellow_mat, -0.65),
        ("two_wheeler", "2W", two_wheeler_mat, -1.85),
    ]
    for vehicle_type, label, mat, y_offset in button_specs:
        add_cube(f"vehicle_selector_button_{vehicle_type}", (panel_x - 0.18, panel_y + y_offset, panel_z - 0.05), (0.12, 1.05, 0.48), mat)
        add_text(
            f"vehicle_selector_button_{vehicle_type}_text",
            label,
            (panel_x - 0.26, panel_y + y_offset, panel_z + 0.02),
            0.2,
            white_mat,
            rotation=(math.radians(90), 0, math.pi),
            align="CENTER",
        )

    vehicle_selector_status_text = add_text(
        "vehicle_selector_status_text",
        f"ACTIVE\n{VEHICLE_LABELS[active_vehicle_type].upper()}",
        (panel_x - 0.18, panel_y - 2.65, panel_z + 1.05),
        0.22,
        hologram_green_mat,
        rotation=(math.radians(90), 0, math.pi),
        align="CENTER",
    )


def create_selected_simulation_vehicle(vehicle_type="car"):
    """Rebuild the selected animated road vehicle on the centered single lane."""
    vehicle_type = normalize_vehicle_type(vehicle_type)
    remove_objects_by_prefix("selected_sim_")

    if vehicle_type == "bus":
        material = bus_yellow_mat
        start_y, stop_y, end_y = -54, -17.0, 54
    elif vehicle_type == "two_wheeler":
        material = two_wheeler_mat
        start_y, stop_y, end_y = -49, -13.8, 53
    else:
        material = car_red_mat
        start_y, stop_y, end_y = -50, -14.8, 52

    root = create_main_road_vehicle(
        f"selected_sim_{vehicle_type}",
        material,
        lane_x=MAIN_ROAD_LANE_X,
        start_y=start_y,
        stop_y=stop_y,
        end_y=end_y,
        direction=1,
        vehicle_type=vehicle_type,
    )
    add_text(
        "selected_sim_vehicle_label",
        f"SIMULATING\n{VEHICLE_LABELS[vehicle_type].upper()}",
        (-7.2, -27.0, 2.0),
        0.28,
        score_gold_mat,
        rotation=(math.radians(90), 0, math.pi / 2),
        align="CENTER",
    )
    update_vehicle_selector_status(vehicle_type)
    return root


def unregister_vehicle_selector_ui():
    for cls_name in (
        "SIMULATION_OT_apply_vehicle",
        "SIMULATION_OT_set_vehicle",
        "SIMULATION_PT_vehicle_selector",
    ):
        cls = getattr(bpy.types, cls_name, None)
        if cls:
            try:
                bpy.utils.unregister_class(cls)
            except Exception:
                pass

    if hasattr(bpy.types.Scene, "sim_vehicle_choice"):
        try:
            del bpy.types.Scene.sim_vehicle_choice
        except Exception:
            pass


class SIMULATION_OT_apply_vehicle(bpy.types.Operator):
    bl_idname = "simulation.apply_vehicle"
    bl_label = "Apply Selected Vehicle"
    bl_description = "Build the selected vehicle in the single-lane crossing simulation"

    def execute(self, context):
        vehicle_type = normalize_vehicle_type(context.scene.sim_vehicle_choice)
        create_selected_simulation_vehicle(vehicle_type)
        context.scene.frame_set(FRAME_START)
        return {"FINISHED"}


class SIMULATION_OT_set_vehicle(bpy.types.Operator):
    bl_idname = "simulation.set_vehicle"
    bl_label = "Set Simulation Vehicle"
    bl_description = "Choose and build a vehicle for the single-lane simulation"

    vehicle_type: bpy.props.StringProperty(default="car")

    def execute(self, context):
        vehicle_type = normalize_vehicle_type(self.vehicle_type)
        context.scene.sim_vehicle_choice = vehicle_type
        create_selected_simulation_vehicle(vehicle_type)
        context.scene.frame_set(FRAME_START)
        return {"FINISHED"}


class SIMULATION_PT_vehicle_selector(bpy.types.Panel):
    bl_label = "Vehicle Simulation"
    bl_idname = "SIMULATION_PT_vehicle_selector"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Simulation"

    def draw(self, context):
        layout = self.layout
        layout.prop(context.scene, "sim_vehicle_choice", text="Vehicle")
        layout.operator("simulation.apply_vehicle", text="Apply Vehicle")

        row = layout.row(align=True)
        op = row.operator("simulation.set_vehicle", text="Car")
        op.vehicle_type = "car"
        op = row.operator("simulation.set_vehicle", text="Bus")
        op.vehicle_type = "bus"
        op = row.operator("simulation.set_vehicle", text="Two Wheeler")
        op.vehicle_type = "two_wheeler"


def register_vehicle_selector_ui():
    unregister_vehicle_selector_ui()
    bpy.types.Scene.sim_vehicle_choice = bpy.props.EnumProperty(
        name="Vehicle",
        description="Vehicle to animate through the single-lane crossing",
        items=VEHICLE_SELECTOR_ITEMS,
        default="car",
    )
    bpy.utils.register_class(SIMULATION_OT_apply_vehicle)
    bpy.utils.register_class(SIMULATION_OT_set_vehicle)
    bpy.utils.register_class(SIMULATION_PT_vehicle_selector)
    scene.sim_vehicle_choice = "car"


create_vehicle_selector_panel("car")
create_selected_simulation_vehicle("car")


# =====================================================
# TOLL LANE VEHICLES (EAST SERVICE ROAD, NORTHBOUND)
# =====================================================
def create_toll_lane_vehicle(name, material, lane_y, start_x, stop_x, end_x, vehicle_type, move_keys):
    """Animate a vehicle along +X through the toll lane."""
    root = add_empty(f"{name}_ROOT", (start_x, lane_y, 0))

    if vehicle_type == "car":
        wheels = create_realistic_car(name, material, root)
    elif vehicle_type == "truck":
        wheels = create_realistic_truck(name, material, root)
    elif vehicle_type == "two_wheeler":
        wheels = create_two_wheeler(name, material, root)
    else:
        wheels = create_realistic_bus(name, material, root)

    for frame, x_pos in move_keys:
        key_location(root, frame, (x_pos, lane_y, 0))
    set_fcurve_interpolation(root, "LINEAR")

    first_frame, last_frame = move_keys[0][0], move_keys[-1][0]
    for wheel in wheels:
        wheel.rotation_euler = (0, 0, 0)
        wheel.keyframe_insert(data_path="rotation_euler", frame=first_frame)
        wheel.rotation_euler = (0, 0, math.radians(1440))
        wheel.keyframe_insert(data_path="rotation_euler", frame=last_frame)
        set_fcurve_interpolation(wheel, "LINEAR")

    return root


TOLL_CAR_X_KEYS = [
    (FRAME_START, 36.0),
    (24, 49.5),
    (42, 49.5),
    (95, 74.0),
    (FRAME_END, 82.0),
]
TOLL_TRUCK_X_KEYS = [
    (10, 28.0),
    (32, 49.5),
    (55, 49.5),
    (120, 74.0),
    (FRAME_END, 80.0),
]
TOLL_BUS_X_KEYS = [
    (18, 40.0),
    (30, 49.5),
    (48, 49.5),
    (110, 72.0),
    (FRAME_END, 78.0),
]

create_toll_lane_vehicle(
    "toll_red_car", car_red_mat, TOLL_VEHICLE_LANE_Y,
    36.0, 49.5, 82.0, "car", TOLL_CAR_X_KEYS,
)
create_toll_lane_vehicle(
    "toll_blue_truck", truck_blue_mat, TOLL_VEHICLE_LANE_Y,
    28.0, 49.5, 80.0, "truck", TOLL_TRUCK_X_KEYS,
)
create_toll_lane_vehicle(
    "toll_yellow_bus", bus_yellow_mat, TOLL_VEHICLE_LANE_Y,
    40.0, 49.5, 78.0, "bus", TOLL_BUS_X_KEYS,
)


# =====================================================
# DECORATION: TREES AND BUILDINGS
# =====================================================
def create_tree(name, x, y, scale=1.0):
    trunk = add_cylinder(f"{name}_trunk", (x, y, 0.7 * scale), 0.16 * scale, 1.4 * scale, tree_trunk_mat, vertices=14)
    leaves = add_uv_sphere(f"{name}_leaves", (x, y, 1.65 * scale), 0.78 * scale, tree_leaf_mat, segments=24, rings=12)
    return trunk, leaves


random.seed(11)
tree_count = 0
while tree_count < 42:
    x = random.uniform(-82, 82)
    y = random.uniform(-55, 55)
    if abs(x) < 12 or abs(y) < 8 or (34 < abs(y) < 49):
        continue
    create_tree(f"scene_tree_{tree_count:02d}", x, y, random.uniform(0.75, 1.35))
    tree_count += 1


def create_building(name, x, y, width, depth, height):
    add_cube(f"{name}_block", (x, y, height * 0.5), (width, depth, height), building_mat)
    add_cube(f"{name}_roof", (x, y, height + 0.18), (width + 0.5, depth + 0.5, 0.36), roof_mat)
    for i, offset in enumerate([-0.3, 0.0, 0.3]):
        add_cube(f"{name}_window_{i}", (x, y - depth * 0.51, height * (0.42 + offset * 0.25)), (width * 0.18, 0.05, height * 0.14), window_mat)


def create_railway_station(name, center_x, platform_y, station_label, platform_length=30, building_side=1):
    platform_x_span = platform_length
    add_cube(f"{name}_platform_slab", (center_x, platform_y, 0.22), (platform_x_span, 3.4, 0.44), platform_mat)
    edge_y = platform_y + (1.55 * building_side)
    add_cube(f"{name}_platform_edge_line", (center_x, edge_y, 0.47), (platform_x_span, 0.22, 0.03), line_yellow_mat)

    building_x = center_x - 8 * building_side
    building_y = platform_y + 5.5 * building_side
    add_cube(f"{name}_station_building", (building_x, building_y, 1.55), (8.5, 5.0, 3.1), station_facade_mat)
    add_cube(f"{name}_station_roof", (building_x, building_y, 3.35), (9.2, 5.7, 0.42), roof_mat)
    add_cube(f"{name}_station_ticket_window", (building_x - 2.8 * building_side, building_y, 1.45), (0.08, 2.0, 1.0), window_mat)

    canopy_x_positions = [center_x - 9, center_x - 3, center_x + 3, center_x + 9]
    for i, canopy_x in enumerate(canopy_x_positions):
        add_cylinder(
            f"{name}_canopy_post_{i}",
            (canopy_x, platform_y + 1.1 * building_side, 1.35),
            0.1, 2.7, black_mat, vertices=16,
        )

    add_cube(
        f"{name}_platform_canopy_roof",
        (center_x, platform_y + 1.35 * building_side, 2.85),
        (platform_x_span - 1.5, 2.2, 0.18),
        roof_mat,
    )

    for bench_x in (center_x - 6, center_x + 6):
        add_cube(f"{name}_bench_seat_{bench_x}", (bench_x, platform_y + 0.55 * building_side, 0.55), (1.8, 0.55, 0.12), wood_mat)
        add_cube(f"{name}_bench_back_{bench_x}", (bench_x, platform_y + 0.95 * building_side, 0.78), (1.8, 0.12, 0.55), wood_mat)

    for lamp_x in (center_x - 12, center_x + 12):
        add_cylinder(f"{name}_platform_lamp_{lamp_x}", (lamp_x, platform_y, 1.6), 0.07, 3.2, black_mat, vertices=12)
        add_uv_sphere(f"{name}_platform_lamp_glow_{lamp_x}", (lamp_x, platform_y, 3.35), 0.22, amber_mat)
        bpy.ops.object.light_add(type="POINT", location=(lamp_x, platform_y, 3.35))
        platform_light = bpy.context.object
        platform_light.name = f"{name}_platform_light_{lamp_x}"
        platform_light.data.color = (1.0, 0.82, 0.45)
        platform_light.data.energy = 220
        platform_light.data.shadow_soft_size = 4

    sign_y = platform_y + 2.2 * building_side
    add_text(
        f"{name}_station_name_sign",
        station_label,
        (center_x, sign_y, 3.15),
        0.55,
        white_mat,
        rotation=(math.radians(90), 0, 0 if building_side > 0 else math.pi),
    )
    add_cube(f"{name}_schedule_board", (center_x + 4 * building_side, platform_y + 0.35 * building_side, 1.55), (0.08, 1.6, 1.1), screen_blue_mat)
    add_text(
        f"{name}_schedule_text",
        "NEXT TRAIN\nAPPROACHING",
        (center_x + 4.05 * building_side, platform_y + 0.35 * building_side, 1.75),
        0.18,
        hologram_blue_mat,
        rotation=(math.radians(90), 0, 0 if building_side > 0 else math.pi),
    )


def create_toll_plaza(name, booth_x, road_y):
    global toll_vehicle_detect_text

    add_cube(f"{name}_toll_lane_pavement", (booth_x, road_y + 2.1, 0.034), (16, 3.0, 0.065), road_edge_mat)
    add_cube(f"{name}_toll_lane_divider", (booth_x, road_y + 1.0, 0.105), (16, 0.12, 0.02), line_white_mat)
    add_cube(f"{name}_toll_stop_line", (booth_x - 5.5, road_y + 2.1, 0.11), (0.28, 2.6, 0.02), line_white_mat)
    add_text(
        f"{name}_lane_mark",
        "TOLL VEHICLES\nNORTH LANE ONLY",
        (booth_x - 10, road_y + 3.2, 0.16),
        0.55,
        line_yellow_mat,
        rotation=(math.radians(90), 0, math.pi / 2),
    )

    gantry_x = TOLL_DETECTION_GANTRY_X
    gantry_y = TOLL_VEHICLE_LANE_Y
    add_cylinder(f"{name}_gantry_post_left", (gantry_x, gantry_y - 2.0, 2.0), 0.1, 4.0, black_mat, vertices=16)
    add_cylinder(f"{name}_gantry_post_right", (gantry_x, gantry_y + 0.35, 2.0), 0.1, 4.0, black_mat, vertices=16)
    add_cube(f"{name}_gantry_beam", (gantry_x, gantry_y - 0.85, 4.05), (0.35, 2.55, 0.28), black_mat)
    add_cube(f"{name}_gantry_sensor_bridge", (gantry_x, gantry_y - 0.85, 3.55), (0.2, 2.35, 0.12), black_mat)
    add_cube(f"{name}_gantry_detection_zone", (gantry_x, gantry_y - 0.2, 2.35), (0.12, 2.1, 1.8), hologram_blue_mat)
    add_cube(f"{name}_gantry_overhead_screen", (gantry_x, gantry_y - 0.85, 3.15), (1.8, 0.08, 0.95), screen_blue_mat)
    toll_vehicle_detect_text = add_text(
        f"{name}_vehicle_detect_display",
        "VEHICLE DETECT\nSCANNING LANE",
        (gantry_x, gantry_y - 0.78, 3.35),
        0.2,
        hologram_green_mat,
        rotation=(math.radians(90), 0, 0),
        align="CENTER",
    )
    for sensor_offset, sensor_label in ((-1.35, "pir"), (-0.2, "ultrasonic")):
        add_uv_sphere(f"{name}_gantry_{sensor_label}_eye", (gantry_x, gantry_y + sensor_offset, 3.45), 0.12, hologram_blue_mat)
        bpy.ops.object.light_add(type="POINT", location=(gantry_x, gantry_y + sensor_offset, 3.45))
        gantry_light = bpy.context.object
        gantry_light.name = f"{name}_gantry_{sensor_label}_light"
        gantry_light.data.color = (0.0, 0.65, 1.0)
        gantry_light.data.energy = 180
        gantry_light.data.shadow_soft_size = 2

    add_cube(f"{name}_toll_booth_base", (booth_x + 4.5, road_y + 5.2, 0.95), (2.8, 2.4, 1.9), toll_booth_mat)
    add_cube(f"{name}_toll_booth_roof", (booth_x + 4.5, road_y + 5.2, 2.15), (3.4, 3.0, 0.35), roof_mat)
    add_cube(f"{name}_toll_booth_window", (booth_x + 3.45, road_y + 5.2, 1.2), (0.08, 1.5, 0.75), window_mat)
    add_cube(f"{name}_toll_rfid_reader", (booth_x + 3.35, road_y + 4.0, 1.35), (0.35, 0.22, 0.55), black_mat)
    add_cube(f"{name}_toll_display_screen", (booth_x + 4.5, road_y + 6.35, 1.85), (1.4, 0.08, 0.55), screen_blue_mat)
    add_text(
        f"{name}_toll_sign",
        "SMART TOLL\nRFID LANE",
        (booth_x + 4.5, road_y + 6.42, 2.2),
        0.22,
        score_gold_mat,
        rotation=(math.radians(90), 0, 0),
    )

    for sensor_name, sensor_x, sensor_y in (
        ("pir_wake_sensor", booth_x - 8.5, TOLL_VEHICLE_LANE_Y),
        ("ultrasonic_type_sensor", booth_x - 2.5, TOLL_VEHICLE_LANE_Y),
    ):
        add_cylinder(f"{name}_{sensor_name}_pole", (sensor_x, sensor_y, 1.1), 0.05, 2.2, black_mat, vertices=12)
        add_cube(f"{name}_{sensor_name}_head", (sensor_x, sensor_y, 2.35), (0.28, 0.22, 0.18), black_mat)
        add_uv_sphere(f"{name}_{sensor_name}_halo", (sensor_x, sensor_y, 2.55), 0.16, hologram_blue_mat)

    pivot = add_empty(f"{name}_toll_barrier_pivot", (booth_x - 1.0, road_y + 2.1, 0.95))
    barrier_arm = add_cube(f"{name}_toll_barrier_arm", (0, 0, 0), (0.16, 4.8, 0.14), white_mat)
    parent_local(barrier_arm, pivot, (0, 2.4, 0))
    for stripe_y in (0.9, 2.0, 3.1, 4.2):
        stripe = add_cube(f"{name}_toll_barrier_stripe_{stripe_y}", (0, 0, 0), (0.2, 0.42, 0.16), barrier_red_mat)
        parent_local(stripe, pivot, (0, stripe_y, 0))

    add_uv_sphere(f"{name}_toll_green_lens", (booth_x - 1.0, road_y + 4.8, 1.55), 0.14, green_mat)
    bpy.ops.object.light_add(type="POINT", location=(booth_x - 1.0, road_y + 4.8, 1.55))
    toll_green_light = bpy.context.object
    toll_green_light.name = f"{name}_toll_open_light"
    toll_green_light.data.color = (0.0, 1.0, 0.18)
    toll_green_light.data.energy = 320
    toll_green_light.data.shadow_soft_size = 3

    open_rotation = (math.radians(82), 0, 0)
    closed_rotation = (0, 0, 0)
    key_rotation(pivot, FRAME_START, closed_rotation)
    key_rotation(pivot, 28, closed_rotation)
    key_rotation(pivot, 42, open_rotation)
    key_rotation(pivot, WARNING_START, open_rotation)
    key_rotation(pivot, GATE_DOWN, closed_rotation)
    key_rotation(pivot, TRAIN_CLEAR, closed_rotation)
    key_rotation(pivot, GATE_UP, open_rotation)
    key_rotation(pivot, FRAME_END, open_rotation)
    set_fcurve_interpolation(pivot, "BEZIER")

    for frame, energy in ((FRAME_START, 0), (42, 480), (WARNING_START, 480), (GATE_DOWN, 0), (GATE_UP, 480), (FRAME_END, 480)):
        toll_green_light.data.energy = energy
        toll_green_light.data.keyframe_insert(data_path="energy", frame=frame)


def create_bus_lane_and_stop():
    add_cube("bottom_bus_lane_north_edge", (0, BUS_LANE_Y + 1.65, 0.108), (146, 0.16, 0.018), line_yellow_mat)
    add_cube("bottom_bus_lane_south_edge", (0, BUS_LANE_Y - 1.65, 0.108), (146, 0.16, 0.018), line_yellow_mat)
    add_cube("bottom_bus_stop_pull_in", (BUS_STOP_X, BUS_LANE_Y, 0.11), (15.0, 3.25, 0.02), hologram_green_mat)

    for index, mark_x in enumerate((-68, -34, 0, 34, 68)):
        add_text(
            f"bottom_bus_lane_marking_{index}",
            "BUS\nONLY",
            (mark_x, BUS_LANE_Y - 0.45, 0.15),
            0.58,
            line_yellow_mat,
            rotation=(0, 0, math.pi / 2),
            align="CENTER",
        )

    platform_y = BUS_LANE_Y + 5.15
    add_cube("bus_stop_platform", (BUS_STOP_X, platform_y, 0.22), (16.0, 2.45, 0.42), platform_mat)
    add_cube("bus_stop_platform_edge", (BUS_STOP_X, platform_y - 1.08, 0.47), (15.5, 0.18, 0.04), line_yellow_mat)

    for post_x in (BUS_STOP_X - 6.4, BUS_STOP_X - 2.2, BUS_STOP_X + 2.2, BUS_STOP_X + 6.4):
        add_cylinder(f"bus_stop_canopy_post_{post_x}", (post_x, platform_y, 1.35), 0.08, 2.7, black_mat, vertices=14)

    add_cube("bus_stop_canopy_roof", (BUS_STOP_X, platform_y, 2.85), (15.2, 2.65, 0.22), roof_mat)
    add_cube("bus_stop_bench_seat", (BUS_STOP_X - 3.8, platform_y + 0.2, 0.6), (3.2, 0.5, 0.14), wood_mat)
    add_cube("bus_stop_bench_back", (BUS_STOP_X - 3.8, platform_y + 0.56, 0.95), (3.2, 0.12, 0.7), wood_mat)

    add_cube("bus_stop_overhead_display", (BUS_STOP_X + 3.0, platform_y - 0.98, 3.45), (5.4, 0.12, 1.05), screen_blue_mat)
    add_text(
        "bus_stop_overhead_display_text",
        "BUS STOP\nROUTE B1\nETA 02 MIN",
        (BUS_STOP_X + 3.0, platform_y - 1.08, 3.55),
        0.22,
        hologram_green_mat,
        rotation=(math.radians(90), 0, 0),
        align="CENTER",
    )
    add_text(
        "bus_stop_name_ground_mark",
        "BUS STOP",
        (BUS_STOP_X, BUS_LANE_Y + 0.85, 0.15),
        0.6,
        line_white_mat,
        rotation=(0, 0, math.pi / 2),
        align="CENTER",
    )

    bus_root = add_empty("bottom_bus_lane_bus_ROOT", (-82, BUS_LANE_Y, 0))
    wheels = create_realistic_bus("bottom_bus_lane_bus", bus_yellow_mat, bus_root)
    bus_keyframes = [
        (FRAME_START, -82),
        (55, BUS_STOP_X),
        (110, BUS_STOP_X),
        (170, -20),
        (FRAME_END, 82),
    ]
    for frame, x_pos in bus_keyframes:
        key_location(bus_root, frame, (x_pos, BUS_LANE_Y, 0))
    set_fcurve_interpolation(bus_root, "LINEAR")

    for wheel in wheels:
        wheel.rotation_euler = (0, 0, 0)
        wheel.keyframe_insert(data_path="rotation_euler", frame=FRAME_START)
        wheel.rotation_euler = (0, 0, math.radians(900))
        wheel.keyframe_insert(data_path="rotation_euler", frame=55)
        wheel.rotation_euler = (0, 0, math.radians(900))
        wheel.keyframe_insert(data_path="rotation_euler", frame=110)
        wheel.rotation_euler = (0, 0, math.radians(2200))
        wheel.keyframe_insert(data_path="rotation_euler", frame=FRAME_END)
        set_fcurve_interpolation(wheel, "LINEAR")


def create_delivery_system():
    yard_x = DELIVERY_HUB_X
    yard_y = DELIVERY_HUB_Y
    add_cube("delivery_yard_pavement", (yard_x, yard_y, 0.035), (27.0, 11.5, 0.07), road_edge_mat)
    add_cube("delivery_loading_dock", (yard_x + 6.0, yard_y - 2.85, 0.75), (12.0, 2.0, 1.5), platform_mat)
    add_cube("delivery_warehouse_block", (yard_x + 6.0, yard_y - 5.7, 2.35), (12.5, 4.0, 4.7), building_mat)
    add_cube("delivery_warehouse_roof", (yard_x + 6.0, yard_y - 5.7, 4.88), (13.2, 4.5, 0.34), roof_mat)
    add_cube("delivery_warehouse_shutter", (yard_x + 1.3, yard_y - 3.62, 1.55), (3.2, 0.1, 2.45), chrome_mat)
    add_cube("delivery_dispatch_display", (yard_x + 9.1, yard_y - 3.55, 3.15), (4.8, 0.12, 1.05), screen_blue_mat)
    add_text(
        "delivery_dispatch_display_text",
        "DELIVERY HUB\nLORRY READY\nLOAD 3 PARCELS",
        (yard_x + 9.1, yard_y - 3.67, 3.25),
        0.19,
        hologram_green_mat,
        rotation=(math.radians(90), 0, 0),
        align="CENTER",
    )
    add_text(
        "delivery_hub_sign",
        "DELIVERY\nSYSTEM",
        (yard_x + 6.0, yard_y - 3.62, 4.35),
        0.34,
        score_gold_mat,
        rotation=(math.radians(90), 0, 0),
        align="CENTER",
    )

    lorry_root = add_empty("delivery_lorry_ROOT", (yard_x - 6.8, yard_y + 1.0, 0))
    lorry_wheels = create_realistic_truck("delivery_lorry", delivery_lorry_mat, lorry_root)
    lorry_side_text = add_text(
        "delivery_lorry_side_text",
        "DELIVERY",
        (0, 0, 0),
        0.34,
        white_mat,
        rotation=(math.radians(90), 0, 0),
        align="CENTER",
    )
    parent_local(lorry_side_text, lorry_root, (0, -1.18, 1.9), rotation=(math.radians(90), 0, 0))

    for frame, x_pos in ((FRAME_START, yard_x - 9.5), (70, yard_x - 6.8), (135, yard_x - 6.8), (FRAME_END, yard_x + 1.2)):
        key_location(lorry_root, frame, (x_pos, yard_y + 1.0, 0))
    set_fcurve_interpolation(lorry_root, "LINEAR")
    for wheel in lorry_wheels:
        wheel.rotation_euler = (0, 0, 0)
        wheel.keyframe_insert(data_path="rotation_euler", frame=FRAME_START)
        wheel.rotation_euler = (0, 0, math.radians(720))
        wheel.keyframe_insert(data_path="rotation_euler", frame=70)
        wheel.rotation_euler = (0, 0, math.radians(720))
        wheel.keyframe_insert(data_path="rotation_euler", frame=135)
        wheel.rotation_euler = (0, 0, math.radians(1500))
        wheel.keyframe_insert(data_path="rotation_euler", frame=FRAME_END)
        set_fcurve_interpolation(wheel, "LINEAR")

    parcel_positions = [
        (yard_x + 1.0, yard_y - 1.3, 1.7),
        (yard_x + 2.0, yard_y - 1.25, 1.7),
        (yard_x + 3.0, yard_y - 1.4, 1.7),
        (yard_x - 0.4, yard_y - 0.9, 0.35),
        (yard_x + 0.5, yard_y - 0.85, 0.35),
    ]
    for idx, pos in enumerate(parcel_positions):
        add_cube(f"delivery_parcel_{idx}", pos, (0.78, 0.58, 0.48), wood_mat)


def create_road_barricade(name, center_x, center_y, span=10, along_x=True, accident=False):
    sign_text = "ACCIDENT AHEAD\nSLOW / DIVERT" if accident else "ROAD WORK\nKEEP LEFT"
    sign_mat = hologram_red_mat if accident else amber_mat

    if along_x:
        offsets = [i * 1.8 for i in range(int(span / 1.8))]
        for i, offset in enumerate(offsets):
            x = center_x - span * 0.5 + offset
            add_cylinder(f"{name}_cone_{i}", (x, center_y, 0.28), 0.18, 0.56, cone_orange_mat, vertices=16)
            if i % 3 == 0:
                add_cube(f"{name}_striped_board_{i}", (x, center_y + 0.55, 0.62), (0.08, 1.2, 0.75), barricade_yellow_mat)
                add_cube(f"{name}_striped_board_red_{i}", (x, center_y + 0.55, 0.62), (0.1, 1.2, 0.16), barrier_red_mat)
    else:
        offsets = [i * 1.8 for i in range(int(span / 1.8))]
        for i, offset in enumerate(offsets):
            y = center_y - span * 0.5 + offset
            add_cylinder(f"{name}_cone_{i}", (center_x, y, 0.28), 0.18, 0.56, cone_orange_mat, vertices=16)
            if i % 3 == 0:
                add_cube(f"{name}_striped_board_{i}", (center_x + 0.55, y, 0.62), (1.2, 0.08, 0.75), barricade_yellow_mat)
                add_cube(f"{name}_striped_board_red_{i}", (center_x + 0.55, y, 0.62), (1.2, 0.1, 0.16), barrier_red_mat)

    sign_x = center_x - (span * 0.55 if along_x else 0)
    sign_y = center_y + (0 if along_x else span * 0.55)
    add_cube(f"{name}_warning_sign_post", (sign_x, sign_y, 1.0), (0.12, 0.12, 2.0), black_mat)
    add_cube(f"{name}_warning_sign_board", (sign_x + (0.75 if along_x else 0), sign_y + (0.75 if not along_x else 0), 1.85), (1.1, 0.08, 0.75), sign_board_mat)
    add_text(
        f"{name}_warning_sign_text",
        sign_text,
        (sign_x + (0.8 if along_x else 0), sign_y + (0.8 if not along_x else 0), 2.05),
        0.2,
        sign_mat,
        rotation=(math.radians(90), 0, 0 if along_x else math.radians(90)),
    )


def create_emergency_gate_barricade(name, pivot_x, pivot_y, arm_direction=1):
    add_cylinder(f"{name}_post", (pivot_x, pivot_y, 1.05), 0.14, 2.1, post_mat, vertices=20)
    add_cube(f"{name}_base", (pivot_x, pivot_y, 0.16), (0.85, 0.85, 0.32), ballast_mat)
    arm_pivot = add_empty(f"{name}_arm_pivot", (pivot_x, pivot_y, 1.85))
    arm = add_cube(f"{name}_arm", (0, 0, 0), (5.5, 0.16, 0.16), barricade_yellow_mat)
    parent_local(arm, arm_pivot, (arm_direction * 2.75, 0, 0))
    for local_x in (1.0, 2.3, 3.6, 4.9):
        stripe = add_cube(f"{name}_arm_stripe_{local_x}", (0, 0, 0), (0.45, 0.2, 0.18), barrier_red_mat)
        parent_local(stripe, arm_pivot, (arm_direction * local_x, 0, 0))

    open_rotation = (0, math.radians(75 * arm_direction), 0)
    closed_rotation = (0, 0, 0)
    key_rotation(arm_pivot, FRAME_START, open_rotation)
    key_rotation(arm_pivot, WARNING_START - 5, open_rotation)
    key_rotation(arm_pivot, WARNING_START, closed_rotation)
    key_rotation(arm_pivot, TRAFFIC_RESUME, closed_rotation)
    key_rotation(arm_pivot, TRAFFIC_RESUME + 10, open_rotation)
    key_rotation(arm_pivot, FRAME_END, open_rotation)
    set_fcurve_interpolation(arm_pivot, "BEZIER")


def create_railway_tunnel(name, start_x, end_x):
    global tunnel_emergency_text

    length = end_x - start_x
    center_x = (start_x + end_x) / 2
    tunnel_height = 5.6
    tunnel_width = 9.2
    south_face_y = -4.8

    add_cube(f"{name}_interior_dark", (center_x, 0, 2.0), (length - 1.0, tunnel_width - 1.4, 3.8), tunnel_dark_mat)
    add_cube(f"{name}_south_wall", (center_x, -tunnel_width / 2, tunnel_height / 2), (length + 2.0, 0.9, tunnel_height), tunnel_concrete_mat)
    add_cube(f"{name}_north_berm", (center_x, tunnel_width / 2 + 0.8, 1.2), (length + 2.0, 2.4, 2.4), grass_dark_mat)
    add_cube(f"{name}_roof_shell", (center_x, 0, tunnel_height + 0.25), (length + 2.2, tunnel_width + 1.6, 0.75), tunnel_concrete_mat)

    for portal_x, portal_name in ((start_x, "west_portal"), (end_x, "east_portal")):
        add_cube(f"{name}_{portal_name}_left_pillar", (portal_x, -tunnel_width / 2 + 0.5, 2.2), (0.9, 0.9, 4.4), tunnel_concrete_mat)
        add_cube(f"{name}_{portal_name}_right_pillar", (portal_x, tunnel_width / 2 - 0.5, 2.2), (0.9, 0.9, 4.4), tunnel_concrete_mat)
        add_cube(f"{name}_{portal_name}_lintel", (portal_x, 0, 4.75), (1.2, tunnel_width + 0.4, 0.7), tunnel_concrete_mat)
        add_text(
            f"{name}_{portal_name}_label",
            "RAIL TUNNEL",
            (portal_x, south_face_y, 3.2),
            0.45,
            white_mat,
            rotation=(math.radians(90), 0, math.pi / 2),
        )

    add_cube(f"{name}_emergency_panel", (center_x, -tunnel_width / 2 + 0.55, 2.0), (0.1, 1.8, 1.2), screen_blue_mat)
    tunnel_emergency_text = add_text(
        f"{name}_emergency_oled",
        "TUNNEL STATUS\nNORMAL",
        (center_x, -tunnel_width / 2 + 0.48, 2.25),
        0.18,
        tunnel_emergency_mat,
        rotation=(math.radians(90), 0, math.pi / 2),
        align="LEFT",
    )
    add_cube(f"{name}_gas_sensor", (center_x - 4, -tunnel_width / 2 + 0.7, 2.8), (0.25, 0.18, 0.18), black_mat)
    add_cube(f"{name}_temp_sensor", (center_x + 4, -tunnel_width / 2 + 0.7, 2.8), (0.25, 0.18, 0.18), black_mat)
    add_cylinder(f"{name}_gsm_antenna", (center_x, south_face_y - 1.2, 3.8), 0.05, 2.6, black_mat, vertices=12)
    add_uv_sphere(f"{name}_gsm_module", (center_x, south_face_y - 1.2, 5.2), 0.22, hologram_blue_mat)

    for fan_offset in (-6, 0, 6):
        fan_x = center_x + fan_offset
        add_cylinder(f"{name}_vent_fan_{fan_offset}", (fan_x, tunnel_width / 2 - 0.8, 3.6), 0.55, 0.12, black_mat, vertices=24, rotation=(math.radians(90), 0, 0))

    # Tunnel interior lights
    for light_offset in (-8, -3, 3, 8):
        lx = center_x + light_offset
        bpy.ops.object.light_add(type="POINT", location=(lx, 0, 4.0))
        tun_light = bpy.context.object
        tun_light.name = f"{name}_interior_light_{light_offset}"
        tun_light.data.color = (1.0, 0.75, 0.35)
        tun_light.data.energy = 120
        tun_light.data.shadow_soft_size = 4


# =====================================================
# BUILD THE WORLD — call all creation functions
# =====================================================
create_building("north_east_building_1", 22, 28, 6, 5, 4.5)
create_building("north_east_building_2", 32, 30, 7, 6, 5.5)
create_building("south_west_building_1", -25, -28, 5.5, 5, 3.8)
create_building("south_west_building_2", -18, -32, 8, 5, 6.0)
create_building("far_east_building", 48, 25, 6, 6, 7.0)
create_building("far_west_building", -42, 28, 5, 4, 4.0)

create_railway_station("south_station", -45, -8.5, "SOUTH HALT", platform_length=28, building_side=-1)

create_toll_plaza("east_toll", TOLL_BOOTH_X, TOLL_ROAD_Y)
create_bus_lane_and_stop()
create_delivery_system()

create_road_barricade("west_road_work", -32, -50.5, span=8, along_x=True, accident=False)
create_road_barricade("east_accident", 28, 43, span=7, along_x=True, accident=True)

create_emergency_gate_barricade("emergency_gate_north", -8.5, 7.5, arm_direction=1)
create_emergency_gate_barricade("emergency_gate_south", 8.5, -7.5, arm_direction=-1)

create_railway_tunnel("east_tunnel", 62, 82)


# =====================================================
# CAMERA
# =====================================================
bpy.ops.object.camera_add(location=(32, -38, 28))
camera = bpy.context.object
camera.name = "main_overview_camera"
camera.data.lens = 28
camera.data.clip_end = 500
look_at(camera, (0, 2, 0))
scene.camera = camera

# Secondary camera angle
bpy.ops.object.camera_add(location=(-18, -22, 12))
cam2 = bpy.context.object
cam2.name = "crossing_close_up_camera"
cam2.data.lens = 35
cam2.data.clip_end = 300
look_at(cam2, (0, 0, 1.5))


# =====================================================
# LIGHTING — cinematic three-point + environment
# =====================================================
# Sun light (key light)
bpy.ops.object.light_add(type="SUN", location=(20, -15, 35))
sun = bpy.context.object
sun.name = "sun_key_light"
sun.data.energy = 4.5
sun.data.color = (1.0, 0.95, 0.85)
sun.data.angle = math.radians(2.5)
sun.rotation_euler = (math.radians(55), math.radians(15), math.radians(-25))

# Fill light (softer, blue-ish)
bpy.ops.object.light_add(type="SUN", location=(-20, 20, 25))
fill = bpy.context.object
fill.name = "fill_light_blue"
fill.data.energy = 1.8
fill.data.color = (0.75, 0.85, 1.0)
fill.rotation_euler = (math.radians(65), math.radians(-20), math.radians(30))

# Rim / back light
bpy.ops.object.light_add(type="AREA", location=(-40, -5, 18))
rim = bpy.context.object
rim.name = "rim_backlight"
rim.data.energy = 450
rim.data.color = (1.0, 0.88, 0.65)
rim.data.size = 12
look_at(rim, (0, 0, 0))

# Ambient area light near crossing
bpy.ops.object.light_add(type="AREA", location=(0, 0, 15))
ambient = bpy.context.object
ambient.name = "crossing_ambient_fill"
ambient.data.energy = 180
ambient.data.color = (1.0, 0.95, 0.9)
ambient.data.size = 20

# Street lamps along the main road
for lamp_y in (-35, -20, 20, 35):
    for lamp_x in (-7.5, 7.5):
        bpy.ops.object.light_add(type="POINT", location=(lamp_x, lamp_y, 4.8))
        sl = bpy.context.object
        sl.name = f"street_lamp_{lamp_x}_{lamp_y}"
        sl.data.color = (1.0, 0.85, 0.55)
        sl.data.energy = 160
        sl.data.shadow_soft_size = 3
        add_cylinder(f"street_lamp_pole_{lamp_x}_{lamp_y}", (lamp_x, lamp_y, 2.2), 0.06, 4.4, black_mat, vertices=12)
        add_uv_sphere(f"street_lamp_globe_{lamp_x}_{lamp_y}", (lamp_x, lamp_y, 4.55), 0.22, amber_mat)


# =====================================================
# WORLD ENVIRONMENT (Sky gradient)
# =====================================================
world = bpy.data.worlds.new("cinematic_sky_world")
scene.world = world


def setup_world_environment(world_ref):
    try:
        world_ref.use_nodes = True
        world_tree = getattr(world_ref, "node_tree", None)
        if world_tree is None:
            world_ref.color = (0.28, 0.52, 0.88)
            print("[INFO] World node_tree unavailable; using plain sky color.")
            return False

        world_tree.nodes.clear()

        bg_node = world_tree.nodes.new("ShaderNodeBackground")
        bg_node.location = (0, 0)
        bg_node.inputs["Color"].default_value = (0.28, 0.52, 0.88, 1.0)
        bg_node.inputs["Strength"].default_value = 0.6

        output_node = world_tree.nodes.new("ShaderNodeOutputWorld")
        output_node.location = (300, 0)

        world_tree.links.new(bg_node.outputs["Background"], output_node.inputs["Surface"])
        return True
    except Exception as exc:
        world_ref.color = (0.28, 0.52, 0.88)
        print(f"[INFO] World node setup skipped safely: {exc}")
        return False


WORLD_NODES_ENABLED = setup_world_environment(world)


# =====================================================
# IOT TELEMETRY — FRAME CHANGE HANDLER
# =====================================================
TELEMETRY_LOG = []


def compute_telemetry(frame):
    """Return a dict of IoT telemetry for the current frame."""
    t = frame / FPS
    train_x = train_root.location.x
    crossing_x = 0.0
    distance_m = max(0, (crossing_x - train_x) * 2.0)

    if frame < WARNING_START:
        gate_state = "OPEN"
        risk = "LOW"
    elif frame < GATE_DOWN:
        gate_state = "CLOSING"
        risk = "MEDIUM"
    elif frame < TRAIN_CLEAR:
        gate_state = "CLOSED"
        risk = "HIGH"
    elif frame < GATE_UP:
        gate_state = "OPENING"
        risk = "MEDIUM"
    else:
        gate_state = "OPEN"
        risk = "LOW"

    return {
        "ts": int(time.time() * 1000),
        "frame": frame,
        "sim_time_s": round(t, 2),
        "train_x": round(train_x, 2),
        "train_distance_m": round(distance_m, 1),
        "gate_state": gate_state,
        "risk_level": risk,
        "warning_active": WARNING_START <= frame <= TRAIN_CLEAR,
        "train_in_crossing": GATE_DOWN <= frame <= TRAIN_CLEAR,
    }


def send_thingsboard_telemetry(payload):
    if not THINGSBOARD_ENABLED:
        return
    url = f"{THINGSBOARD_HOST}/api/v1/{THINGSBOARD_ACCESS_TOKEN}/telemetry"
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=TELEMETRY_TIMEOUT_SECONDS) as resp:
            pass
    except Exception as e:
        print(f"[ThingsBoard] send error: {e}")


@persistent
def on_frame_change(scene_ref):
    frame = scene_ref.frame_current

    if frame % TELEMETRY_SEND_EVERY_N_FRAMES != 0:
        return

    telemetry = compute_telemetry(frame)
    TELEMETRY_LOG.append(telemetry)

    # Update live IoT text objects
    try:
        dist_obj = bpy.data.objects.get("iot_live_distance_text")
        if dist_obj and dist_obj.data:
            dist_obj.data.body = f"TRAIN DISTANCE\n{telemetry['train_distance_m']:.0f} m"
    except Exception:
        pass

    try:
        status_obj = bpy.data.objects.get("iot_live_status_text")
        if status_obj and status_obj.data:
            link = "THINGSBOARD" if THINGSBOARD_ENABLED else "SIM MODE"
            status_obj.data.body = (
                f"LINK: {link}\n"
                f"GATE: {telemetry['gate_state']}\n"
                f"RISK: {telemetry['risk_level']}\n"
                f"FRAME: {frame}"
            )
    except Exception:
        pass

    send_thingsboard_telemetry(telemetry)

    if STORE_TELEMETRY_IN_BLENDER_TEXT:
        text_name = "IoT_Telemetry_Log"
        if text_name not in bpy.data.texts:
            bpy.data.texts.new(text_name)
        bpy.data.texts[text_name].write(json.dumps(telemetry) + "\n")


# Register handler without clearing unrelated handlers from the Blender session.
bpy.app.handlers.frame_change_post[:] = [
    handler
    for handler in bpy.app.handlers.frame_change_post
    if getattr(handler, "__name__", "") != "on_frame_change"
]
bpy.app.handlers.frame_change_post.append(on_frame_change)


# =====================================================
# GLB EXPORT (optional)
# =====================================================
if EXPORT_GLB_ON_RUN:
    glb_path = bpy.path.abspath(GLB_EXPORT_PATH) if bpy.data.filepath else os.path.join(os.path.expanduser("~"), "railway_crossing_iot_scene.glb")
    try:
        bpy.ops.export_scene.gltf(
            filepath=glb_path,
            export_format="GLB",
            export_animations=True,
            export_lights=True,
            export_cameras=True,
        )
        print(f"[GLB] Exported to: {glb_path}")
    except Exception as e:
        print(f"[GLB] Export error: {e}")


# =====================================================
# FINAL SETUP
# =====================================================
register_vehicle_selector_ui()
scene.frame_set(FRAME_START)
print("=" * 60)
print("  RAILWAY CROSSING 3D SIMULATION — READY")
print(f"  Frames: {FRAME_START} to {FRAME_END} @ {FPS} fps")
print(f"  Render output: {render_abs_dir}")
print(f"  ThingsBoard: {'ENABLED' if THINGSBOARD_ENABLED else 'DISABLED (sim mode)'}")
print("  Vehicle selector: View3D sidebar > Simulation > Vehicle Simulation")
print(f"  Press SPACE in the 3D viewport to play the animation.")
print("=" * 60)
