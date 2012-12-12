#!/usr/bin/env pypy

import random
import math
import argparse
import cPickle as pickle
import logging
import os
import sys
import re
import colorsys
from xml.dom.minidom import parse

import Image
import ImageDraw

# local
from sfgen import NameCurve

def ensure_python():
    # pylab doesn't play well with pypy
    # so this will cause us to re-exec if
    # we are in pypy ... do not use if you want pypy
    if sys.subversion[0] == "PyPy":
        msg = "Restarting within CPython environment to accomdate scipy/numpy"
        log(msg)
        args = ["python", "python"] + sys.argv
        os.execlp("/usr/bin/env", *args)

logging.basicConfig(format="%(asctime)s (%(process)d): %(message)s", level=logging.DEBUG, datefmt='%d/%m/%y %H:%M:%S')
log = logging.info

X_SCALE_FACTOR = (1.0 / math.sqrt(3))

def avg_stdev(data):
    avg = sum(data) / float(len(data))
    stdev = math.sqrt(sum((x - avg) ** 2 for x in data) / float(len(data)))
    return (avg, stdev)

class CrystalEnvironment(dict):
    def __init__(self, *args, **kw):
        self._init_defaults()
        self.update(*args, **kw)
        self.factory_settings = self.copy()

    def __getattr__(self, name):
        return self[name]

    def __getstate__(self):
        return dict(self)

    def __setstate__(self, state):
        self.update(state)

    def get_default(self, key):
        return self.factory_settings[key]

    def randomize(self):
        for key in self:
            if key == "sigma":
                continue
            self[key] += (random.choice([1, -1]) * (random.random() / random.randint(10, 1000)))

    def _init_defaults(self):
        # (3a) 
        # "A boundary site with 1 or 2 attached neighbors needs boundary mass at least beta to join the crystal
        #  This is the case when the local mesoscopic geometry near x corresponds to a tip or flat spot of the crystal. 
        #  (Distinguishing the two cases turns out to be of minor significance.) In our simulations, beta is typically 
        #  between about 1.05 and 3. We assume beta > 1 since 1 is the basic threshold of the case to follow next.
        self["beta"] = 1.3

        # (3b)
        # "A boundary site with 3 attached neighbors joins the crystal if either it has boundary mass >= 1, 
        #  or it has diffusive mass < theta in its neighborhood and it has boundary mass >= alpha"
        self["theta"] = 0.025
        self["alpha"] = 0.08

        # (2) 
        # "Proportion kappa of the diffusive mass at each boundary site crystallizes. 
        #  The remainder (proportion 1 - kappa) becomes boundary mass."
        self["kappa"] = 0.003

        # (4)
        # "Proportion mu of the boundary mass and proportion upsilon of the crystal mass at each boundary site become diffusive mass. 
        #  Melting represents mass flow at the boundary from ice and quasi-liquid back to vapor, reverse
        #  effects from the freezing of step ii. Typically mu is small and upsilon extremely small."
        self["mu"] = 0.07
        self["upsilon"] = 0.00005

        # (5)
        # "The diffusive mass at each site undergoes an independent random perturbation of proportion sigma"
        self["sigma"] = 0.00001

        # initial diffusion
        self["gamma"] = 0.5

	def _init_special(self):
		pass

class DefaultFlake(CrystalEnvironment):
    pass

class SpikeEndFlake(CrystalEnvironment):
    def __init__(self):
        super(SpikeEndFlake, self).__init__()
        self["beta"] = 0.8
        self["gamma"] = 0.34
        self["mu"] = 0.09

class FernFlake(CrystalEnvironment):
    def __init__(self):
        super(FernFlake, self).__init__()
        self["beta"] = 0.8
        self["gamma"] = 0.52
        self["mu"] = 0.09

class ClassicFlake(CrystalEnvironment):
    def __init__(self):
        super(ClassicFlake, self).__init__()
        self["beta"] = 0.9
        self["gamma"] = 0.58

class DelicateFlake(CrystalEnvironment):
    def __init__(self):
        super(DelicateFlake, self).__init__()
        self["beta"] = 0.95
        self["gamma"] = 0.3

class RandomBeautyFlake(CrystalEnvironment):
    def __init__(self):
        super(RandomBeautyFlake, self).__init__()
        self["beta"] = 1.30
        self["gamma"] = 0.50

class CrystalLattice(object):
    def __init__(self, size, environment=None, celltype=None, max_steps=0, margin=None, curves=None):
        self.size = size
        if environment == None:
            environment = CrystalEnvironment()
        self.environment = environment
        if celltype == None:
            celltype = SnowflakeCell
        self.celltype = celltype
        self.iteration = 1
        assert margin > 0 and margin <= 1.0
        self.margin = margin
        self.curves = curves
        self.max_steps = max_steps
        self._init_cells()

    def __setstate__(self, state):
        # 0.1->0.2 format changes
        if "radius" in state:
            state["size"] = state["radius"]
            del state["radius"]
        if "angle" in state:
            del state["angle"]
        self.__dict__.update(state)

    def save_lattice(self, fn):
        msg = "Saving %s..." % fn
        log(msg)
        f = open(fn, 'wb')
        pickle.dump(self, f)

    @classmethod
    def load_lattice(cls, fn):
        msg = "Loading %s..." % fn
        log(msg)
        f = open(fn, 'rb')
        obj = pickle.load(f)
        for cell in obj.cells:
            cell.lattice = obj
            cell.env = obj.environment
        return obj

    def get_neighbors(self, xy):
        (x, y) = xy
        nlist = [(x, y + 1), (x, y - 1), (x - 1, y), (x + 1, y), (x - 1, y - 1), (x + 1, y + 1)]
        nlist = map(self._cell_index, filter(self._xy_ok, nlist))
        res = tuple([self.cells[nidx] for nidx in nlist if self.cells[nidx] != None])
        return res

    def reality_check(self):
        for cell in self.cells:
            cell.reality_check()

    def _init_cells(self):
        self.cells = [None] * (self.size * self.size)
        for x in range(self.size):
            for y in range(self.size):
                xy = (x, y)
                cell = self.celltype(xy, self)
                idx = self._cell_index(xy)
                self.cells[idx] = cell
        self.reality_check()
        center_pt = self._cell_index((self.size / 2, self.size / 2))
        self.cells[center_pt].attach(1)

    def _xy_ok(self, xy):
        (x, y) = xy
        return (x >= 0 and x < self.size and y >= 0 and y < self.size)

    def _cell_index(self, xy):
        (x, y) = xy
        return y * self.size + x

    def _cell_xy(self, idx):
        y = idx / self.size
        x = idx % self.size
        return (x, y)

    def adjust_humidity(self, val):
        for cell in self.cells:
            if cell.attached or cell.boundary:
                continue
            # we use the same coef as the noise coef
            cell.diffusive_mass += val * self.environment.sigma * cell.diffusive_mass
    
    def adjust_temperature(self, val):
        self.environment.beta = .01 * val * self.environment.get_default("beta") + self.environment.get_default("beta")
        self.environment.theta = .001 * val * self.environment.get_default("theta") + self.environment.get_default("theta")
        self.environment.alpha = .005 * val * self.environment.get_default("alpha") + self.environment.get_default("alpha")
        self.environment.kappa = .0001 * val * self.environment.get_default("kappa") + self.environment.get_default("kappa")
        self.environment.mu = .0005 * val * self.environment.get_default("mu") + self.environment.get_default("mu")
        self.environment.upsilon = .000005 * val * self.environment.get_default("upsilon") + self.environment.get_default("upsilon")

    def print_status(self):
        dm = sum([cell.diffusive_mass for cell in self.cells if cell])
        cm = sum([cell.crystal_mass for cell in self.cells if cell])
        bm = sum([cell.boundary_mass for cell in self.cells if cell])
        acnt = len([cell for cell in self.cells if cell and cell.attached])
        bcnt = len([cell for cell in self.cells if cell and cell.boundary])
        msg = "Step #%d, %d attached, %d boundary, %.2f dM, %.2f bM, %.2f cM, tot %.2f M" % (self.iteration, acnt, bcnt, dm, bm, cm, dm + cm + bm)
        log(msg)

    def step(self):
        for cell in self.cells:
            if cell == None or cell.attached:
                continue
            cell.step_one()
        for cell in self.cells:
            if cell == None or cell.attached:
                continue
            cell.step_two()
        for cell in self.cells:
            if cell == None or cell.attached:
                continue
            cell.step_three()
        # run curves
        self.iteration += 1
        if self.curves:
            self.adjust_temperature(self.curves.get_temperature(self.iteration))
            self.adjust_humidity(self.curves.get_humidity(self.iteration))

    def translate_xy(self, xy):
        (x, y) = xy
        x = int(round(x * X_SCALE_FACTOR))
        return (x, y)

    def polar_to_xy(self, args):
        (angle, distance) = args
        half = self.size / 2.0
        angle = math.radians(angle)
        y = int(round(half - (math.sin(angle) * distance)))
        x = int(round(half + (math.cos(angle) * distance)))
        return (x, y)

    def xy_to_polar(self, args):
        (x, y) = args
        half = self.size / 2.0
        x -= half
        y += half
        angle = math.degrees(math.atan2(y, x))
        distance = math.hypot(x, y)
        return (angle, distance)

    def snowflake_radius(self, angle=135):
        # we cast a ray on the 135 degeree axis
        radius = 0
        half = self.size / 2.0
        while radius < half:
            radius += 1
            xy = self.polar_to_xy((angle, radius))
            cell = self.cells[self._cell_index(xy)]
            if cell.attached or cell.boundary:
                continue
            return radius
        # uhh
        return int(round(half))

    def crop_snowflake(self, margin=None):
        def scale(val):
            return int(round(X_SCALE_FACTOR * val))
        if margin == None:
            margin = 15
        half = self.size / 2
        radius = scale(self.snowflake_radius())
        distance = min(radius + margin, half)
        half_s = scale(half)
        distance_s = scale(distance)
        box = (half_s - distance, half - distance, half_s + distance, half + distance)
        return box

    def headroom(self, margin=None):
        if self.max_steps and self.iteration >= self.max_steps:
            return False
        if margin == None:
            margin = self.margin
        assert margin > 0 and margin <= 1
        cutoff = int(round(margin * (self.size / 2.0)))
        radius = self.snowflake_radius()
        if radius > cutoff:
            return False
        return True

    def grow(self):
        while True:
            self.step()
            if self.iteration % 50 == 0:
                self.print_status()
                if not self.headroom():
                    break

    def save_laser_image(self, fn, layer_cnt):
        import scipy.cluster.vq
        import numpy
        acells = [cell for cell in self.cells if cell and cell.attached]
        fm = [cell.crystal_mass for cell in acells]
        clusters = scipy.cluster.vq.kmeans2(numpy.array(fm), layer_cnt)
        cluster_map = clusters[1]
        layers = []
        color = (255, 255, 255)
        for layer in range(layer_cnt):
            img = Image.new("RGB", (self.size, self.size))
            layers.append(img)

        for (layer, cell) in zip(cluster_map, acells):
            xy = cell.xy
            layer = layers[layer]
            layer.putpixel(xy, color)

        fns = []
        for (idx, img) in enumerate(layers):
            img = img.rotate(45)
            img = img.resize((int(round(self.size * X_SCALE_FACTOR)), int(self.size)))
            img = img.crop(self.crop_snowflake())
            y_sz = int(round((self.size / float(img.size[0])) * img.size[1]))
            img = img.resize((self.size, y_sz))
            tmpfn = fn.split('.')[0] + ("_layer_%d." % (idx + 1)) + str.join('.', fn.split('.')[1:])
            msg = "Saving LaserImage %s..." % tmpfn
            log(msg)
            img.save(tmpfn)
            fns.append(tmpfn)
        return fns

    def save_image(self, fn, bw=False, boundary=False, margin=None):
        if bw:
            parts = fn.split(".")
            parts[0] += "_bw"
            fn = str.join(".", parts)
        msg = "Saving %s..." % fn
        log(msg)
        img = Image.new("RGB", (self.size, self.size))
        for (idx, cell) in enumerate(self.cells):
            if cell == None:
                continue
            xy = self._cell_xy(idx)
            color = (0, 0, 0)
            if boundary and cell.boundary or cell.attached:
                if bw:
                    color = 0xff
                    color = (color, color, color)
                else:
                    color = 200 * cell.crystal_mass
                    color = min(255, int(color))
                    color = (color, color, color)
            elif not bw:
                color = 200 * cell.diffusive_mass
                color = min(255, int(color))
                color = (color, color, color)
            #color = tuple([int(round(x * 0xff)) for x in colorsys.hsv_to_rgb(cell.age / float(self.iteration), 1, 1)])
            img.putpixel(xy, color)
        img = img.rotate(45)
        img = img.resize((int(round(self.size * X_SCALE_FACTOR)), int(self.size)))
        img = img.crop(self.crop_snowflake(margin=margin))
        y_sz = int(round((self.size / float(img.size[0])) * img.size[1]))
        assert y_sz == self.size, "This is critical for laser cutting."
        img = img.resize((self.size, y_sz))
        # 
        img.save(fn)

class SnowflakeCell(object):
    def __init__(self, xy, lattice):
        self.xy = xy
        self.lattice = lattice
        self.env = lattice.environment
        self.diffusive_mass = self.env.gamma
        self.boundary_mass = 0.0
        self.crystal_mass = 0.0
        self.attached = False
        self.age = 0
        self.__neighbors = None

    def __getstate__(self):
        return (self.xy, self.diffusive_mass, self.boundary_mass, self.crystal_mass, self.attached, self.age)

    def __setstate__(self, state):
        self.xy = state[0]
        self.diffusive_mass = state[1]
        self.boundary_mass = state[2]
        self.crystal_mass = state[3]
        self.attached = state[4]
        # 0.2 -> 0.3
        try:
            self.age = state[5]
        except IndexError:
            self.age = 0
        self.__neighbors = None
        self.lattice = None
        self.env = None

    def reality_check(self):
        assert len(self.neighbors)
        for neighbor in self.neighbors:
            assert self in neighbor.neighbors, "%s not in %s" % (str(self), str(neighbor.neighbors))

    def __repr__(self):
        return "(%d,%d)" % self.xy

    @property
    def neighbors(self):
        if self.__neighbors == None:
            self.__neighbors = self.lattice.get_neighbors(self.xy)
        return self.__neighbors
    
    @property
    def attached_neighbors(self):
        return [cell for cell in self.neighbors if cell.attached]

    @property
    def boundary(self):
        return (not self.attached) and any([cell.attached for cell in self.neighbors])

    def step_one(self):
        self._next_dm = self.diffusion_calc()

    def step_two(self):
        self.diffusive_mass = self._next_dm
        self.attachment_flag = self.attached
        self.freezing_step()
        self.attachment_flag = self.attachment_step()
        self.melting_step()

    def step_three(self):
        if self.boundary and self.attachment_flag:
            self.attach()
        self.noise_step()

    def diffusion_calc(self):
        next_dm = self.diffusive_mass
        if self.attached:
            return next_dm
        self.age += 1
        for cell in self.neighbors:
            if cell.attached:
                next_dm += self.diffusive_mass
            else:
                next_dm += cell.diffusive_mass
        return float(next_dm) / (len(self.neighbors) + 1)

    def attach(self, offset=0.0):
        self.crystal_mass = self.boundary_mass + self.crystal_mass + offset
        self.boundary_mass = 0
        self.attached = True

    def freezing_step(self):
        if not self.boundary:
            return
        self.boundary_mass += (1 - self.env.kappa) * self.diffusive_mass
        self.crystal_mass += (self.env.kappa * self.diffusive_mass)
        self.diffusive_mass = 0

    def attachment_step(self):
        if not self.boundary:
            return False
        if len(self.attached_neighbors) <= 2:
            if self.boundary_mass > self.env.beta:
                return True
        elif len(self.attached_neighbors) == 3:
            if self.boundary_mass >= 1:
                return True
            else:
                summed_diffusion = self.diffusive_mass
                for cell in self.neighbors:
                    summed_diffusion += cell.diffusive_mass
                if summed_diffusion < self.env.theta and self.boundary_mass >= self.env.alpha:
                    return True
        elif len(self.attached_neighbors) >= 4:
            return True
        return False
    
    def melting_step(self):
        if not self.boundary:
            return
        self.diffusive_mass += self.env.mu * self.boundary_mass + self.env.upsilon * self.crystal_mass
        self.boundary_mass = (1 - self.env.mu) * self.boundary_mass
        self.crystal_mass = (1 - self.env.upsilon) * self.crystal_mass

    def noise_step(self):
        if (self.boundary or self.attached):
            return
        if random.choice([True, False]):
            self.diffusive_mass = (1 - self.env.sigma) * self.diffusive_mass
        else:
            self.diffusive_mass = (1 + self.env.sigma) * self.diffusive_mass

DEFAULTS = {
    "size": 200,
    "name": "snowflake",
    "bw": False,
    "env": '',
    "pipeline_3d": False,
    "pipeline_lasercutter": False,
    "randomize": False,
    "max_steps": 0,
    "margin": .42,
    "curves": False,
}

def get_cli():
    parser = argparse.ArgumentParser(description='Snowflake Generator.')
    parser.add_argument(dest="name", nargs='+', help="The name of the snowflake.")
    parser.add_argument('-s', '--size', dest="size", type=int, help="The size of the snowflake.")
    parser.add_argument('-e', '--env', dest='env', help='comma seperated key=val env overrides')
    parser.add_argument('-b', '--bw', dest='bw', action='store_true', help='write out the image in black and white')
    parser.add_argument('-r', '--randomize', dest='randomize', action='store_true', help='randomize environment.')
    parser.add_argument('-x', '--extrude', dest='pipeline_3d', action='store_true', help='Enable 3d pipeline.')
    parser.add_argument('-l', '--laser', dest='pipeline_lasercutter', action='store_true', help='Enable Laser Cutter pipeline.')
    parser.add_argument('-M', '--max-steps', dest='max_steps', type=int, help='Maximum number of iterations.')
    parser.add_argument('-m', '--margin', dest='margin', type=float, help='When to stop snowflake growth (between 0 and 1)')
    parser.add_argument('-c', '--curves', dest='curves', action='store_true', help='run name as curves')

    parser.set_defaults(**DEFAULTS)
    args = parser.parse_args()
    args.name = str.join('', map(str.lower, args.name))
    if args.name[-1] == '/':
        # wart from the shell
        args.name = args.name[:-1]
    if not os.path.exists(args.name):
        os.mkdir(args.name)
    if args.pipeline_lasercutter:
        ensure_python()
    if args.pipeline_3d:
        args.bw = True
    return args

def check_basecut(svgfn):
    # ensure there is only one path
    svg = parse(svgfn)
    for (cnt, node) in enumerate(svg.getElementsByTagName("path")):
        if cnt > 0:
            return False
    return True

def merge_svg(file_list, color_list, outfn):
    first = None
    idx = 0
    for (svgfn, color) in zip(file_list, color_list):
        svg = parse(svgfn)
        for node in svg.getElementsByTagName("g"):
            if idx == 0:
                node.attributes["fill"] = "None"
                node.attributes["stroke"] = color
                node.attributes["stroke-width"] = "10"
            else:
                node.attributes["fill"].nodeValue = color
            idx += 1
            if first == None:
                first = svg
                break
            import_nodes = svg.importNode(node, True)
            first.childNodes[1].appendChild(import_nodes)
    f = open(outfn, 'w')
    f.write(first.toxml())

def potrace(svgfn, fn, turd=None):
    if turd:
        # "turd" supression, important for the cut layer
        cmd = "potrace -i -b svg -t %s -o %s %s" % (turd, svgfn, fn)
    else:
        cmd = "potrace -i -b svg -o %s %s" % (svgfn, fn)
    msg = "Running '%s'" % cmd
    log(msg)
    os.system(cmd)

# laser cutter pipeline
def pipeline_lasercutter(args, cl, turd=10):
    lifn = "%s_laser.bmp" % args.name
    svgfn = "%s_laser_test.svg" % args.name
    bwfn = "%s_laser_bw.bmp" % args.name
    llist = cl.save_laser_image(lifn, 3)
    # try to save o'natural
    cl.save_image(lifn, bw=True, boundary=False)
    potrace(svgfn, bwfn)
    if not check_basecut(svgfn):
        msg = "There are disconnected elements in the base cut, turning on boundary layer."
        log(msg)
        cl.save_image(lifn, bw=True, boundary=True)
        potrace(svgfn, bwfn, turd=2000)
        assert check_basecut(svgfn), "Despite best efforts, base cut is still non-contiguous."
    os.unlink(svgfn)
    llist.insert(0, bwfn)
    colors = ["#ff0000", "#00ff00", "#0000ff", "#ff00ff", "#ffff00", "#00ffff"]
    svgs = []
    for (idx, fn) in enumerate(llist):
        svgfn = os.path.splitext(fn)[0]
        svgfn = "%s_laser.svg" % svgfn
        svgs.append(svgfn)
        if idx == 0:
            potrace(svgfn, fn, turd=turd)
        else:
            potrace(svgfn, fn)
    outfn = "%s_laser_merged.svg" % args.name
    merge_svg(svgs, colors, outfn)

# 3d pipeline
def pipeline_3d(args):
    cmd = "potrace -M .1 --tight -i -b eps -o %s.eps %s_bw.bmp" % (args.name, args.name)
    msg = "Running '%s'" % cmd
    log(msg)
    os.system(cmd)
    #
    cmd = "pstoedit -dt -f dxf:-polyaslines %s.eps %s.dxf" % (args.name, args.name)
    msg = "Running '%s'" % cmd
    log(msg)
    os.system(cmd)
    #
    scad_fn = "%s.scad" % args.name
    f = open(scad_fn, 'w')
    scad_txt = 'scale([30, 30, 30]) linear_extrude(height=.25, layer="0") import("%s.dxf");\n' % args.name
    f.write(scad_txt)
    f.close()
    cmd = "/Applications/OpenSCAD.app/Contents/MacOS/OpenSCAD -o %s.stl %s" % (args.name, scad_fn)
    msg = "Running '%s'" % cmd
    log(msg)
    os.system(cmd)
    #
    cmd = "python /Applications/Cura/Cura.app/Contents/Resources/Cura/cura.py -s %s.stl -i snowflake.ini" % args.name
    msg = "Running '%s'" % cmd
    log(msg)
    os.system(cmd)

def choose_environment(name):
    ftypes = [cls for cls in globals().values() if type(cls) == type and issubclass(cls, CrystalEnvironment) and cls != CrystalEnvironment]
    return ftypes[len(name) % len(ftypes)]

def run(args):
    msg = "Snowflake Generator v0.3"
    log(msg)
    pfn = "%s.pickle" % args.name
    ifn = "%s.png" % args.name
    if os.path.exists(pfn):
        cl = CrystalLattice.load_lattice(pfn)
        cl.save_image(ifn, bw=args.bw)
    else:
        kw = {}
        if args.env:
            mods = {key: float(val) for (key, val) in [keyval.split('=') for keyval in args.env.split(',')]}
            env = CrystalEnvironment(mods)
            kw["environment"] = env
        elif args.randomize:
            env = CrystalEnvironment()
            env.randomize()
            msg = str.join(', ', ["%s=%.6f" % (key, env[key]) for key in env])
            log(msg)
            kw["environment"] = env
        elif args.curves:
            # choose environment
            env = choose_environment(args.name)
            msg = "%s selected %s environment." % (args.name, env.__name__)
            log(msg)
            kw["environment"] = env()
            curves = NameCurve(args.name)
            curves.run_graph()
            kw["curves"] = curves
        kw["max_steps"] = args.max_steps
        kw["margin"] = args.margin
        cl = CrystalLattice(args.size, **kw)
        try:
            cl.grow()
        finally:
            cl.save_lattice(pfn)
            cl.save_image(ifn, bw=args.bw)
    if args.pipeline_3d:
        pipeline_3d(args)
    if args.pipeline_lasercutter:
        pipeline_lasercutter(args, cl)

if __name__ == "__main__":
    args = get_cli()
    os.chdir(args.name)
    try:
        run(args)
    finally:
        os.chdir('..')