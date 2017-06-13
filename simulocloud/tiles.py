"""
tile
"""
import numpy as np
import itertools
import simulocloud.pointcloud
import simulocloud.exceptions

class Tile(simulocloud.pointcloud.PointCloud):
    """An immmutable pointcloud"""
    def __init__(self, xyz, header=None):
        """."""
        super(Tile, self).__init__(xyz, header)
        self._arr.flags.writeable = False
    
    @property
    def arr(self):
        """Get, but not set, the underlying (x, y, z) array of point coordinates."""
        return self._arr
    
    @arr.setter
    def arr(self, value):
        raise simulocloud.exceptions.TileException("Tile pointcloud cannot be modified")

class TilesGrid(object):
    """Container for tiles grid."""
    def __init__(self, tiles, edges, validate=True):
        """Directly initialise `TilesGrid` from grids
        
        Arguments
        ---------
        tiles: `numpy.ndarray` (ndim=3, dtype=object)
            3D array containing pointclouds (usually of type `Tile`) spatially
            seperated by uniform edges ordered along array axes:
                 0:x, 1:y, 2:z
        edges: `numpy.ndarray` (ndim=4, dtype=float)
            three 3D x, y and z coordinate arrays concatenated in 4th axis
            defining edges between (and around) `tiles`
        
        """
        self.tiles = tiles
        self.edges = edges
        if validate:
            if not self.validate():
                msg = "Tiles do not fit into edges grid"
                raise simulocloud.exceptions.TilesGridException(msg)

    def __getitem__(self, key):
        """Return a subset of TilesGrid instance using numpy-like indexing.
        
        Notes
        -----
        - Steps are forbidden; only contiguous TilesGrids can be created
        - Negative steps are forbidden
        
        """
        # Coerce key to list
        try:
            key = list(key)
        except TypeError:
            key = [key]
        
        # Freeze slice indices to shape of tiles array
        key_ = []
        for sl, nd in itertools.izip_longest(key, self.tiles.shape,
                                             fillvalue=slice(None)):
            try: # assume slice
                start, stop, step = sl.indices(nd)
            except AttributeError: # coerce indices to slice
                if sl is None:
                    start, stop, step = slice(None).indices(nd)
                else: # single element indexing
                   start, stop, step = slice(sl, sl+1).indices(nd)
            
            if not step == 1:
                raise ValueError("TilesGrid must be contiguous, slice step must be 1")
            
            key_.append(slice(start, stop))
         
        # Extend slice stops by 1 for edges array
        ekey = [slice(sl.start, sl.stop+1) if sl.stop - sl.start
                else slice(sl.start, sl.stop) # dont create edges where no tiles
                for sl in key_]
        
        return type(self)(self.tiles[key_], self.edges[ekey], validate=False)
        # bounds = edges[0,0,0], edges[-1,-1,-1]

    def __len__(self):
        """Return the number of elements in tiles grid."""
        return self.tiles.size

    def __nonzero__(self):
        """Return True if there are any tiles."""
        return bool(len(self))

    @classmethod
    def from_splitlocs(cls, pcs, splitlocs):
        """Construct `TilesGrid` instance by retiling pointclouds.
        
        Arguments
        ---------
        pcs: seq of `simulocloud.pointcloud.Pointcloud`
        splitlocs: dict {d: dlocs, ...}, where:
            d: str
                'x', 'y' and/or 'z' dimension
            dlocs: list
                locations along specified axis at which to split
                (see docs for `simulocloud.pointcloud.PointCloud.split`)
        
            dimensions can be omitted, resulting in no splitting in that
            dimension
        
        Returns
        -------
        `TilesGrid` instance
            internal edges defined by `splitlocs`, grid bounds equal to merged
            bounds of `pcs`
        """
        # Sort splitlocs and determine their bounds
        mins, maxs = [],[]
        for d in 'xyz':
            dlocs = sorted(splitlocs.get(d, []))
            try:
                mind, maxd = dlocs[0], dlocs[-1]
            except IndexError:
                mind, maxd = np.inf, -np.inf # always within another bounds
            splitlocs[d] = dlocs
            mins.append(mind), maxs.append(maxd)
        
        # Ensure grid will be valid
        splitloc_bounds = simulocloud.pointcloud.Bounds(*(mins + maxs))
        pcs_bounds = simulocloud.pointcloud.merge_bounds([pc.bounds for pc in pcs])
        if not simulocloud.pointcloud._inside_bounds(splitloc_bounds, pcs_bounds):
            raise ValueError("Split locations must be within total bounds of pointclouds")
        
        tiles = retile(pcs, splitlocs, pctype=Tile)
        edges = make_edges_grid(pcs_bounds, splitlocs)
        
        return cls(tiles, edges, validate=False)

    @property
    def bounds(self):
        """The bounds of the entire grid of tiles."""
        bounds = np.concatenate([self.edges[0,0,0], self.edges[-1,-1,-1]])
        return simulocloud.pointcloud.Bounds(*bounds)

    @property
    def shape(self):
        """Return the shape of the grid of tiles."""
        return self.tiles.shape
    
    def validate(self):
        """Return True if grid edges accurately describes tiles."""
        for ix, iy, iz in itertools.product(*map(xrange, self.tiles.shape)):
            # Ensure pointcloud bounds fall within edges
            tile = self.tiles[ix, iy, iz]
            for compare, edges, bounds in zip(
                    (np.less_equal, np.greater_equal), # both edges inclusive due to outermost edges
                    (self.edges[ix, iy, iz], self.edges[ix+1, iy+1, iz+1]),
                    (tile.bounds[:3], tile.bounds[3:])): # mins, maxs
                for edge, bound in zip(edges, bounds):
                    if not compare(edge, bound):
                        return False
        
        return True

def retile(pcs, splitlocs, pctype=Tile):
    """Return a 3D grid of (merged) pointclouds split in x, y and z dimensions.
    
    Arguments
    ---------
    pcs: seq of `simulocloud.pointcloud.PointCloud`
    splitlocs: dict
        {d: dlocs, ...}, where:
            d: str
                'x', 'y' and/or 'z' dimension
            dlocs: list
                locations along specified axis at which to split
                (see docs for `PointCloud.split`)
        dimensions can be omitted, resulting in no splitting in that dimension
    pctype: subclass of `simulocloud.pointcloud.PointCloud`
       type of pointclouds to return (`simulocloud.pointcloud.PointCloud`)
    
    Returns
    -------
    tile_grid: `numpy.ndarray` (ndim=3, dtype=object)
        3D array containing pointclouds (of type `pctype`) resulting from the
        (collective) splitting of `pcs` in each dimension according to `dlocs`
        in `splitlocs`
        sorted `dlocs` align with sequential pointclouds along each array axis:
            0:x, 1:y, 2:z
    
    """
    shape = [] #nx, ny, nz
    for d in 'x', 'y', 'z':
        dlocs = sorted(splitlocs.setdefault(d, []))
        shape.append(len(dlocs) + 1) #i.e. n pointclouds created by split
        #! Should assert splitlocs within bounds of pcs
        splitlocs[d] = dlocs
    
    # Build 4D array with pcs split in x, y and z
    tile_grid = np.empty([len(pcs)] + shape, dtype=object)
    for i, pc in enumerate(pcs):
        pcs = pc.split('x', splitlocs['x'], pctype=pctype)
        for ix, pc in enumerate(pcs):
            pcs = pc.split('y', splitlocs['y'])
            for iy, pc in enumerate(pcs):
                pcs = pc.split('z', splitlocs['z'])
                # Assign pc to predetermined location
                for iz, pc in enumerate(pcs):
                    tile_grid[i, ix, iy, iz] = pc
    
    # Flatten to 3D
    return np.sum(tile_grid, axis=0)

def fractional_splitlocs(bounds, nx=None, ny=None, nz=None):
    """Generate locations to split bounds into n even sections per axis.
    
    Arguments
    ---------
    bounds: `simulocloud.pointcloud.Bounds` (or similiar)
        bounds within which to create tiles
    nx, ny, nz : int (default=None)
        number of pointclouds desired along each axis
        no splitting if n < 2 (or None)
    
    Returns
    -------
    splitlocs: dict ({d: dlocs, ...)}
        lists of locations for each dimension d (i.e. 'x', 'y', 'z')
        len(dlocs) = nd-1; omitted if nd=None
     
    """
    bounds = simulocloud.pointcloud.Bounds(*bounds) #should be a strict bounds (min<max, etc)
    nsplits = {d: n for d, n in zip('xyz', (nx, ny, nz)) if n is not None}
    # Build splitlocs
    splitlocs = {}
    for d, nd in nsplits.iteritems():
        mind, maxd = simulocloud.pointcloud._get_dimension_bounds(bounds, d)
        splitlocs[d] = np.linspace(mind, maxd, num=nd,
                                   endpoint=False)[1:] # "inside" edges only
    
    return splitlocs

def make_edges_grid(bounds, splitlocs):
    """Return coordinate array describing the edges between retiled pointclouds.
    
    Arguments
    ---------
    bounds: `simulocloud.pointcloud.Bounds` or similiar
       (minx, miny, minz, maxx, maxy, maxz) bounds of entire grid
    splitlocs: dict {d: dlocs, ...}
        same as argument to `retile`
    
    Returns
    -------
    edges_grid: `numpy.ndarray` (ndim=4, dtype=float)
        4D array containing x, y and z coordinate arrays (see documentation for
        `numpy.meshgrid`), indexed by 'ij' and concatenated in 4th dimension
        indices, such that `edges_grid[ix, iy, iz, :]` returns a single point
        coordinate in the form `array([x, y, z])`
    
    Notes and Examples
    ------------------
    This function is intended to be used alongside `retile` with the same
    `splitlocs` and `bounds` equal to those of the pointcloud (or merged bounds
    of pointclouds) to be retiled. The resultant `edges` grid provides a
    spatial description of the pointclouds in the `tiles` grid:
    - the coordinates at `edges[ix, iy, iz]` lies between the two adjacent
      pointclouds `tiles[ix-1, iy-1, iz-1], tiles[ix, iy, iz]`
    - `edges[ix, iy, iz]` and `edges[ix+1, iy+1, iz+1]` combine to form a set
      of bounds which contain --- but are not (necessarily) equal to --- those
      of the pointcloud at `tile_grid[ix, iy, iz]`
     
    >>> splitlocs = fractional_splitlocs(pc.bounds, nx=10, ny=8, nz=5)
    >>> tiles = retile(pc, splitlocs)
    >>> edges = make_edges_grid(pc.bounds, splitlocs)
    >>> print tiles.shape, edges.shape # +1 in each axis
    (10, 8, 5) (11, 9, 6, 3)
    >>> ix, iy, iz = 5, 3, 2
    # Show edge between tile pointclouds
    >>> print (tiles[ix-1, iy-1, iz-1].bounds[3:], # upper bounds
    ...        edges[ix, iy, iz],
    ...        tiles[ix, iy, iz].bounds[:3]) # lower bounds
    ((14.99, 24.98, 1.98), array([ 15.,  25.,   2.]), (15.01, 25.02, 2.09))
    >>> # Show bounds around tile
    >>> print tiles[ix, iy, iz].bounds
    Bounds: minx=15, miny=25, minz=2.09
            maxx=16, maxy=26.2, maxz=2.99
    >>> print Bounds(*np.concatenate([edges[ix, iy, iz],
    ...                               edges[ix+1, iy+1, iz+1]]))
    Bounds: minx=15, miny=25, minz=2
            maxx=16, maxy=26.2, maxz=3
    
    """
    #! Should fail if splitlocs is not within bounds
    
    # Determine bounds for each tile in each dimension
    edges = []
    for d in 'xyz':
        d_edges = []
        mind, maxd = simulocloud.pointcloud._get_dimension_bounds(bounds, d)
        dlocs = np.array(splitlocs.setdefault(d, np.array([])))
        edges.append(np.concatenate([[mind], dlocs, [maxd]]))
    
    # Grid edge coordinates
    grids = np.meshgrid(*edges, indexing='ij')
    grids = [DD[..., np.newaxis] for DD in grids] # promote to 4D
    return np.concatenate(grids, axis=3)
