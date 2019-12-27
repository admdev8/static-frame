

import typing as tp

from itertools import zip_longest
from itertools import chain
from functools import partial
import numpy as np


from static_frame.core.util import NULL_SLICE
from static_frame.core.util import UNIT_SLICE
from static_frame.core.util import DTYPE_OBJECT
# from static_frame.core.util import EMPTY_TUPLE
from static_frame.core.util import DTYPE_BOOL

from static_frame.core.util import INT_TYPES
from static_frame.core.util import KEY_ITERABLE_TYPES
from static_frame.core.util import KEY_MULTIPLE_TYPES
# from static_frame.core.util import DTYPE_INT_DEFAULT
from static_frame.core.util import DTYPE_NAN_KIND

from static_frame.core.util import GetItemKeyType
from static_frame.core.util import GetItemKeyTypeCompound
from static_frame.core.util import DtypeSpecifier
from static_frame.core.util import UFunc

from static_frame.core.util import row_1d_filter
from static_frame.core.util import column_2d_filter

from static_frame.core.util import mloc
from static_frame.core.util import array_shift
from static_frame.core.util import full_for_fill
from static_frame.core.util import resolve_dtype
from static_frame.core.util import resolve_dtype_iter
from static_frame.core.util import dtype_to_na
from static_frame.core.util import array_to_groups_and_locations
from static_frame.core.util import isna_array
from static_frame.core.util import slice_to_ascending_slice
from static_frame.core.util import binary_transition
from static_frame.core.util import ufunc_axis_skipna
from static_frame.core.util import shape_filter
from static_frame.core.util import array2d_to_tuples

from static_frame.core.selector_node import InterfaceGetItem
from static_frame.core.util import immutable_filter
from static_frame.core.util import slices_from_targets
from static_frame.core.util import FILL_VALUE_DEFAULT
from static_frame.core.doc_str import doc_inject

from static_frame.core.index_correspondence import IndexCorrespondence

from static_frame.core.display import DisplayConfig
from static_frame.core.display import DisplayActive
from static_frame.core.display import Display

from static_frame.core.container import ContainerOperand

from static_frame.core.exception import ErrorInitTypeBlocks

#-------------------------------------------------------------------------------
class TypeBlocks(ContainerOperand):
    '''An ordered collection of type-heterogenous, immutable NumPy arrays, providing an external array-like interface of a single, 2D array. Used by :py:class:`Frame` for core, unindexed array management.

    A TypeBlocks instance can have a zero size shape (where the length of one axis is zero). Internally, when axis 0 (rows) is of size 0, we store similarly sized arrays. When axis 1 (columns) is of size 0, we do not store arrays, as such arrays do not define a type (as tyupes are defined by columns).
    '''
    # related to Pandas BlockManager
    __slots__ = (
            '_blocks',
            '_dtypes',
            '_index',
            '_shape',
            '_row_dtype',
            'iloc',
            )

    #---------------------------------------------------------------------------
    # constructors

    @classmethod
    def from_blocks(cls,
            raw_blocks: tp.Iterable[np.ndarray],
            shape_reference: tp.Optional[tp.Tuple[int, int]] = None
            ) -> 'TypeBlocks':
        '''
        Main constructor using iterator (or generator) of TypeBlocks; the order of the blocks defines the order of the columns contained.

        It is acceptable to construct blocks with a 0-sided shape.

        Args:
            raw_blocks: iterable (generator compatible) of NDArrays, or a single NDArray.
            shape_reference: optional argument to support cases where no blocks are found in the ``raw_blocks`` iterable, but the outer context is one with rows but no columns, or columns and no rows.

        '''
        blocks: tp.List[np.ndarray] = [] # ordered blocks
        dtypes: tp.List[np.dtype] = [] # column position to dtype
        index: tp.List[tp.Tuple[int, int]] = [] # columns position to blocks key
        block_count = 0

        row_count: tp.Optional[int]

        # if a single block, no need to loop
        if isinstance(raw_blocks, np.ndarray):
            if raw_blocks.ndim > 2:
                raise ErrorInitTypeBlocks('arrays of dimensionality greater than 2 cannot be used to create TypeBlocks')

            row_count, column_count = shape_filter(raw_blocks)
            if column_count == 0:
                # set shape but do not store array
                return cls(blocks=blocks,
                        dtypes=dtypes,
                        index=index,
                        shape=(row_count, column_count)
                        )
            blocks.append(immutable_filter(raw_blocks))
            for i in range(column_count):
                index.append((block_count, i))
                dtypes.append(raw_blocks.dtype)

        else: # an iterable of blocks
            row_count = None
            column_count = 0

            for block in raw_blocks:
                if not isinstance(block, np.ndarray):
                    raise ErrorInitTypeBlocks(f'found non array block: {block}')

                if block.ndim > 2:
                    raise ErrorInitTypeBlocks(f'cannot include array with {block.ndim} dimensions')

                r, c = shape_filter(block)

                # check number of rows is the same for all blocks
                if row_count is not None and r != row_count:
                    raise ErrorInitTypeBlocks(f'mismatched row count: {r}: {row_count}')
                else: # assign on first
                    row_count = r

                # we keep array with 0 rows but > 0 columns, as they take type spce in the TypeBlocks object; arrays with 0 columns do not take type space and thus can be skipped entirely
                if c == 0:
                    continue

                blocks.append(immutable_filter(block))

                # store position to key of block, block columns
                for i in range(c):
                    index.append((block_count, i))
                    dtypes.append(block.dtype)

                column_count += c
                block_count += 1

        # blocks cam be empty
        if row_count is None:
            if shape_reference is not None:
                # if columns have gone to zero, and this was created from a TB that had rows, continue to represent those rows
                row_count = shape_reference[0]
            else:
                raise ErrorInitTypeBlocks('cannot derive a row_count from blocks; provide a shape reference')

        return cls(
                blocks=blocks,
                dtypes=dtypes,
                index=index,
                shape=(row_count, column_count),
                )

    @classmethod
    def from_element_items(cls,
            items: tp.Iterable[tp.Tuple[tp.Tuple[int, ...], object]],
            shape: tp.Tuple[int, ...],
            dtype: np.dtype,
            fill_value: object = FILL_VALUE_DEFAULT
            ) -> 'TypeBlocks':
        '''Given a generator of pairs of iloc coords and values, return a TypeBlock of the desired shape and dtype.
        '''
        fill_value = (fill_value if fill_value is not FILL_VALUE_DEFAULT
                else dtype_to_na(dtype))

        a = np.full(shape, fill_value=fill_value, dtype=dtype)
        for iloc, v in items:
            a[iloc] = v
        a.flags.writeable = False
        return cls.from_blocks(a)

    @classmethod
    def from_zero_size_shape(cls,
            shape: tp.Tuple[int, int] = (0, 0)
            ) -> 'TypeBlocks':
        '''
        Given a shape where one or both axis is 0 (a zero sized array), return a TypeBlocks instance.
        '''
        #NOTE: might want to take dtypes here, so as we can create a zero row Frame with properly defined dtypes. The challenge is that DtypesSpecifier includes column name maps, and we do not have access to an index-like map in this context.

        rows, columns = shape

        if not (rows == 0 or columns == 0):
            raise RuntimeError(f'invalid shape for empty TypeBlocks: {shape}')

        # as types are organized vertically, storing an array with 0 rows but > 0 columns is appropriate as it takes type space
        if rows == 0 and columns > 0:
            a = np.empty(shape)
            a.flags.writeable = False
            return cls.from_blocks(a)

        # for arrays with no width, favor storing shape alone and not creating an array object; the shape will be binding for future appending
        return cls(blocks=list(), dtypes=list(), index=list(), shape=shape)

    #---------------------------------------------------------------------------

    def __init__(self, *,
            blocks: tp.List[np.ndarray],
            dtypes: tp.List[np.dtype],
            index: tp.List[tp.Tuple[int, int]],
            shape: tp.Tuple[int, int]
            ) -> None:
        '''
        Args:
            blocks: A list of one or two-dimensional NumPy arrays
            dtypes: list of dtypes per external column
            index: list of pairs, where the first element is the block index, the second elemetns is the intra-block column
            shape: two-element tuple defining row and column count. A (0, 0) shape is permitted for empty TypeBlocks.
        '''
        self._blocks = blocks
        self._dtypes = dtypes
        self._index = index # list where index, as column, gets block, offset
        self._shape = shape

        if self._blocks:
            self._row_dtype = resolve_dtype_iter(b.dtype for b in self._blocks)
        else:
            # NOTE: this violates the type and may break something downstream; however, this is desirable when appending such that this value does not force an undesirable type resolution
            self._row_dtype = None

        self.iloc = InterfaceGetItem(self._extract_iloc)

    #---------------------------------------------------------------------------
    def __setstate__(self, state: tp.Tuple[object, tp.Mapping[str, tp.Any]]) -> None:
        '''
        Ensure that reanimated NP arrays are set not writeable.
        '''
        for key, value in state[1].items():
            setattr(self, key, value)

        for b in self._blocks:
            b.flags.writeable = False

    def copy(self) -> 'TypeBlocks':
        '''
        Return a new TypeBlocks. Underlying arrays are not copied.
        '''
        return self.__class__(
                blocks=[b for b in self._blocks],
                dtypes=self._dtypes.copy(), # list
                index=self._index.copy(),
                shape=self._shape)

    #---------------------------------------------------------------------------
    # new properties

    @property
    def dtypes(self) -> np.ndarray:
        '''
        Return an immutable array that, for each realizable column (not each block), the dtype is given.
        '''
        # this creates a new array every time it is called; could cache
        a = np.array(self._dtypes, dtype=np.dtype)
        a.flags.writeable = False
        return a

    @property
    def shapes(self) -> np.ndarray:
        '''
        Return an immutable array that, for each block, reports the shape as a tuple.
        '''
        a = np.empty(len(self._blocks), dtype=object)
        a[:] = [b.shape for b in self._blocks]
        a.flags.writeable = False
        return a


    @property # type: ignore
    @doc_inject()
    def mloc(self) -> np.ndarray:
        '''{doc_array}
        '''
        a = np.fromiter(
                (mloc(b) for b in self._blocks),
                count=len(self._blocks),
                dtype=np.int64)
        a.flags.writeable = False
        return a

    @property
    def unified(self) -> bool:
        return len(self._blocks) <= 1

    #---------------------------------------------------------------------------
    # common NP-style properties

    @property
    def shape(self) -> tp.Tuple[int, int]:
        # make this a property so as to be immutable
        return self._shape

    @property
    def ndim(self) -> int:
        return 2

    @property
    def size(self) -> int:
        return sum(b.size for b in self._blocks)

    @property
    def nbytes(self) -> int:
        return sum(b.nbytes for b in self._blocks)

    #---------------------------------------------------------------------------
    # value extraction

    @staticmethod
    def _blocks_to_array(*,
            blocks: tp.Sequence[np.ndarray],
            shape: tp.Tuple[int, int],
            row_dtype: tp.Optional[np.dtype],
            row_multiple: bool
            ) -> np.ndarray:
        '''
        Given blocks and a combined shape, return a consolidated 2D or 1D array.

        Args:
            shape: used in construting returned array; not ussed as a constraint.
            row_multiple: if False, a single row reduces to a 1D
        '''
        # assume column_multiple is True, as this routine is called after handling extraction of single columns
        if len(blocks) == 1:
            if not row_multiple:
                return row_1d_filter(blocks[0])
            else:
                return column_2d_filter(blocks[0])

        # get empty array and fill parts
        # NOTE: row_dtype may be None if a unfillable array; defaults to NP default
        if not row_multiple:
            # return 1 row TypeBlock as a 1D array with length equal to the number of columns
            array = np.empty(shape[1], dtype=row_dtype)
        else: # get ndim 2 shape array
            array = np.empty(shape, dtype=row_dtype)

        pos = 0
        for block in blocks:
            if block.ndim == 1:
                end = pos + 1
            else:
                end = pos + block.shape[1]

            if array.ndim == 1:
                array[pos: end] = block[:] # gets a row from array
            else:
                if block.ndim == 1:
                    array[:, pos] = block[:] # a 1d array
                else:
                    array[:, pos: end] = block[:] # gets a row / row slice from array
            pos = end

        array.flags.writeable = False
        return array


    @property
    def values(self) -> np.ndarray:
        '''Returns a consolidated NP array of the all blocks.
        '''
        # always return a 2D array
        return self._blocks_to_array(
                blocks=self._blocks,
                shape=self._shape,
                row_dtype=self._row_dtype,
                row_multiple=True)

    def axis_values(self, axis: int = 0, reverse: bool = False) -> tp.Iterator[np.ndarray]:
        '''Generator of arrays produced along an axis.

        Args:
            axis: 0 iterates over columns, 1 iterates over rows
        '''
        if axis == 1: # iterate over rows
            unified = self.unified
            # iterate over rows; might be faster to create entire values
            if not reverse:
                row_idx_iter = range(self._shape[0])
            else:
                row_idx_iter = range(self._shape[0] - 1, -1, -1)

            for i in row_idx_iter:
                if unified:
                    b = self._blocks[0]
                    if b.ndim == 1:
                        # single element slice to force array creation (not an element)
                        yield b[i:i+1]
                    else:
                        # if a 2d array, we can yield rows through simple indexing
                        yield b[i]
                else:
                    # cannot use a generator w/ np concat
                    # use == for type comparisons
                    parts = []
                    for b in self._blocks:
                        if b.ndim == 1:
                            # get a slice to permit concatenation
                            key: tp.Union[int, slice] = slice(i, i+1)
                        else:
                            key = i
                        if b.dtype == self._row_dtype:
                            parts.append(b[key])
                        else:
                            parts.append(b[key].astype(self._row_dtype))
                    yield np.concatenate(parts)

        elif axis == 0: # iterate over columns
            if not reverse:
                block_column_iter: tp.Iterable[tp.Tuple[int, int]] = self._index
            else:
                block_column_iter = reversed(self._index)

            for block_idx, column in block_column_iter:
                b = self._blocks[block_idx]
                if b.ndim == 1:
                    yield b
                else:
                    yield b[:, column]
        else:
            raise NotImplementedError()


    def element_items(self) -> tp.Iterator[tp.Tuple[tp.Tuple[int, int], tp.Any]]:
        '''
        Generator of pairs of iloc locations, values accross entire TypeBlock.
        '''
        for iloc in np.ndindex(self._shape):
            block_idx, column = self._index[iloc[1]]
            b = self._blocks[block_idx]
            if b.ndim == 1:
                yield iloc, b[iloc[0]]
            else:
                yield iloc, b[iloc[0], column]

    #---------------------------------------------------------------------------
    # methods for evaluating compatibility with other blocks, and reblocking
    def _reblock_signature(self) -> tp.Generator[tp.Tuple[np.dtype, int], None, None]:
        '''For anticipating if a reblock will result in a compatible block configuration for operator application, get the reblock signature, providing the dtype and size for each block without actually reblocking.

        This is a generator to permit lazy pairwise comparison.
        '''
        group_dtype = None # store type found along contiguous blocks
        group_cols = 0
        for block in self._blocks:
            if group_dtype is None: # first block of a type
                group_dtype = block.dtype
                if block.ndim == 1:
                    group_cols += 1
                else:
                    group_cols += block.shape[1]
                continue
            if block.dtype != group_dtype:
                yield (group_dtype, group_cols)
                group_dtype = block.dtype
                group_cols = 0
            if block.ndim == 1:
                group_cols += 1
            else:
                group_cols += block.shape[1]
        if group_cols > 0:
            yield (group_dtype, group_cols)

    def block_compatible(self,
            other: 'TypeBlocks',
            axis: tp.Optional[int] = None) -> bool:
        '''Block compatible means that the blocks are the same shape. Type is not yet included in this evaluation.

        Args:
            axis: If True, the full shape is compared; if False, only the columns width is compared.
        '''
        # if shape characteristics do not match, blocks cannot be compatible
        if axis is None and self.shape != other.shape:
            return False
        elif axis is not None and self.shape[axis] != other.shape[axis]:
            return False

        for a, b in zip_longest(self._blocks, other._blocks, fillvalue=None):
            if a is None or b is None:
                return False
            if axis is None:
                if shape_filter(a) != shape_filter(b):
                    return False
            else:
                if shape_filter(a)[axis] != shape_filter(b)[axis]:
                    return False
        return True

    def reblock_compatible(self, other: 'TypeBlocks') -> bool:
        '''
        Return True if post reblocking these TypeBlocks are compatible. This only compares columns in blocks, not the entire shape.
        '''
        if self.shape[1] != other.shape[1]:
            return False
        # we only compare size, not the type
        return not any(a is None or b is None or a[1] != b[1]
                for a, b in zip_longest(
                self._reblock_signature(),
                other._reblock_signature()))

    # def unblock_dtype_compatible(self, other: 'TypeBlocks') -> bool:
    #     '''
    #     If, when reduced to single dytpes per column, the blocks are compatible.
    #     '''
    #     compare = resolve_dtype(a, b) in zip(self.dtypes, other.dtypes):



    @classmethod
    def _concatenate_blocks(cls,
            group: tp.Iterable[np.ndarray],
            dtype: DtypeSpecifier = None,
            ) -> np.array:
        '''This will always return a 2D array.
        '''
        # NOTE: if len(group) is 1, can return
        post = np.concatenate([column_2d_filter(x) for x in group], axis=1)
        # NOTE: if give non-native byteorder dtypes, will convert them to native
        if dtype is not None and post.dtype != dtype:
            return post.astype(dtype)
        return post

    @classmethod
    def consolidate_blocks(cls,
            raw_blocks: tp.Iterable[np.ndarray]) -> tp.Generator[np.ndarray, None, None]:
        '''
        Generator consumer, generator producer of np.ndarray, consolidating if types are exact matches.
        '''
        group_dtype = None # store type found along contiguous blocks
        group = []

        for block in raw_blocks:
            if group_dtype is None: # first block of a type
                group_dtype = block.dtype
                group.append(block)
                continue

            # NOTE: could be less strict and look for compatibility within dtype kind (or other compatible types)
            if block.dtype != group_dtype:
                # new group found, return stored
                if len(group) == 1: # return reference without copy
                    # NOTE: using pop() here not shown to be faster
                    yield group[0]
                else: # combine groups
                    # could pre allocating and assing as necessary for large groups
                    yield cls._concatenate_blocks(group, group_dtype)
                group_dtype = block.dtype
                group = [block]
            else: # new block has same group dtype
                group.append(block)

        # always have one or more leftover
        if group:
            if len(group) == 1:
                yield group[0]
            else:
                yield cls._concatenate_blocks(group, group_dtype)


    def _reblock(self) -> tp.Iterator[np.ndarray]:
        '''Generator of new block that consolidate adjacent types that are the same.
        '''
        yield from self.consolidate_blocks(raw_blocks=self._blocks)

    def consolidate(self) -> 'TypeBlocks':
        '''Return a new TypeBlocks that unifies all adjacent types.
        '''
        # note: not sure if we have a single block if we should return a new TypeBlocks instance (as done presently), or simply return self; either way, no new np arrays will be created
        return self.from_blocks(self.consolidate_blocks(raw_blocks=self._blocks))


    def resize_blocks(self, *,
            index_ic: tp.Optional[IndexCorrespondence],
            columns_ic: tp.Optional[IndexCorrespondence],
            fill_value: tp.Any
            ) -> tp.Iterator[np.ndarray]:
        '''
        Given index and column IndexCorrespondence objects, return a generator of resized blocks, extracting from self based on correspondence. Used for Frame.reindex()
        '''
        if columns_ic is None and index_ic is None:
            for b in self._blocks:
                yield b

        elif columns_ic is None and index_ic is not None:
            for b in self._blocks:
                if index_ic.is_subset:
                    # works for both 1d and 2s arrays
                    yield b[index_ic.iloc_src]
                else:
                    shape: tp.Union[int, tp.Tuple[int, int]] = index_ic.size if b.ndim == 1 else (index_ic.size, b.shape[1])
                    values = full_for_fill(b.dtype, shape, fill_value)
                    if index_ic.has_common:
                        values[index_ic.iloc_dst] = b[index_ic.iloc_src]
                    values.flags.writeable = False
                    yield values

        elif columns_ic is not None and index_ic is None:
            if not columns_ic.has_common:
                # just return an empty frame; what type it shold be is not clear
                shape = self.shape[0], columns_ic.size
                values = full_for_fill(self._row_dtype, shape, fill_value)
                values.flags.writeable = False
                yield values
            else:
                if self.unified and columns_ic.is_subset:
                    b = self._blocks[0]
                    if b.ndim == 1:
                        yield b
                    else:
                        yield b[:, columns_ic.iloc_src]
                else:
                    dst_to_src = dict(
                            zip(
                                    tp.cast(tp.Iterable[int], columns_ic.iloc_dst),
                                    tp.cast(tp.Iterable[int], columns_ic.iloc_src),
                            )
                    )
                    for idx in range(columns_ic.size):
                        if idx in dst_to_src:
                            block_idx, block_col = self._index[dst_to_src[idx]]
                            b = self._blocks[block_idx]
                            if b.ndim == 1:
                                yield b
                            else:
                                yield b[:, block_col]
                        else:
                            # just get an empty position
                            # dtype should be the same as the column replacing?
                            values = full_for_fill(self._row_dtype,
                                    self.shape[0],
                                    fill_value)
                            values.flags.writeable = False
                            yield values

        else: # both defined
            assert columns_ic is not None and index_ic is not None
            if not columns_ic.has_common and not index_ic.has_common:
                # just return an empty frame; what type it shold be is not clear
                shape = index_ic.size, columns_ic.size
                values = full_for_fill(self._row_dtype, shape, fill_value)
                values.flags.writeable = False
                yield values
            else:
                if self.unified and index_ic.is_subset and columns_ic.is_subset:
                    b = self._blocks[0]
                    if b.ndim == 1:
                        yield b[index_ic.iloc_src]
                    else:
                        yield b[index_ic.iloc_src_fancy(), columns_ic.iloc_src]
                else:
                    columns_dst_to_src = dict(
                            zip(
                                    tp.cast(tp.Iterable[int], columns_ic.iloc_dst),
                                    tp.cast(tp.Iterable[int], columns_ic.iloc_src),
                            )
                    )

                    for idx in range(columns_ic.size):
                        if idx in columns_dst_to_src:
                            block_idx, block_col = self._index[columns_dst_to_src[idx]]
                            b = self._blocks[block_idx]

                            if index_ic.is_subset:
                                if b.ndim == 1:
                                    yield b[index_ic.iloc_src]
                                else:
                                    yield b[index_ic.iloc_src, block_col]
                            else: # need an empty to fill
                                values = full_for_fill(self._row_dtype,
                                        index_ic.size,
                                        fill_value)
                                if b.ndim == 1:
                                    values[index_ic.iloc_dst] = b[index_ic.iloc_src]
                                else:
                                    values[index_ic.iloc_dst] = b[index_ic.iloc_src, block_col]
                                values.flags.writeable = False
                                yield values
                        else:
                            values = full_for_fill(self._row_dtype,
                                        index_ic.size,
                                        fill_value)
                            values.flags.writeable = False
                            yield values


    def group(self,
            axis: int,
            key: GetItemKeyTypeCompound
            ) -> tp.Iterator[tp.Tuple[np.ndarray, np.ndarray, np.ndarray]]:
        '''
        Args:
            key: iloc selector on opposite axis

        Returns:
            Generator of group, selection pairs, where selection is an np.ndaarray. Returned is as an np.ndarray if key is more than one column.
        '''
        # in worse case this will make a copy of the values extracted; this is probably still cheaper than iterating manually through rows/columns
        unique_axis = None

        if axis == 0:
            # axis 0 means we return row groups; key is a column key
            group_source = self._extract_array(column_key=key)
            if group_source.ndim > 1:
                unique_axis = 0

        elif axis == 1:
            # axis 1 means we return column groups; key is a row key
            group_source = self._extract_array(row_key=key)
            if group_source.ndim > 1 and group_source.shape[0] > 1:
                unique_axis = 1

        groups, locations = array_to_groups_and_locations(
                group_source,
                unique_axis)

        if unique_axis is not None:
            # make the groups hashable for usage in index construction
            if axis == 0:
                groups = array2d_to_tuples(groups)
            elif axis == 1:
                groups = array2d_to_tuples(groups.T)

        for idx, g in enumerate(groups):
            selection = locations == idx
            if axis == 0: # return row extractions
                yield g, selection, self._extract(row_key=selection)
            elif axis == 1: # return columns extractions
                yield g, selection, self._extract(column_key=selection)


    #---------------------------------------------------------------------------
    # transformations resulting in reduced dimensionality

    def ufunc_axis_skipna(self, *,
            skipna: bool,
            axis: int,
            ufunc: UFunc,
            ufunc_skipna: UFunc,
            composable: bool,
            dtypes: tp.Tuple[np.dtype, ...],
            size_one_unity: bool
            ) -> np.ndarray:
        '''Apply a function that reduces blocks to a single axis. Note that this only works in axis 1 if the operation can be applied more than once, first by block, then by reduced blocks. This will not work for a ufunc like argmin, argmax, where the result of the function cannot be compared to the result of the function applied on a different block.

        Args:
            composable: when True, the function application will return a correct result by applying the function to blocks first, and then the result of the blocks (i.e., add, prod); where observation count is relevant (i.e., mean, var, std), this must be False.
            dtype: if we know the return type of func, we can provide it here to avoid having to use the row dtype.

        Returns:
            As this is a reduction of axis where the caller (a Frame) is likely to return a Series, this function is not a generator of blocks, but instead just returns a consolidated 1d array.
        '''
        if axis < 0 or axis > 1:
            raise RuntimeError(f'invalid axis: {axis}')

        func = partial(ufunc_axis_skipna,
                skipna=skipna,
                ufunc=ufunc,
                ufunc_skipna=ufunc_skipna,
                )

        if self.unified:
            result = func(array=column_2d_filter(self._blocks[0]), axis=axis)
            result.flags.writeable = False
            return result
        else:
            if axis == 0:
                # reduce all rows to 1d with column width
                shape: tp.Union[int, tp.Tuple[int, int]] = self._shape[1]
                pos = 0
            elif composable: # axis 1
                # reduce all columns to 2d blocks with 1 column
                shape = (self._shape[0], len(self._blocks))
            else: # axis 1, not block composable
                # Cannot do block-wise processing, must resolve to single array and return
                array = self._blocks_to_array(
                        blocks=self._blocks,
                        shape=self._shape,
                        row_dtype=self._row_dtype,
                        row_multiple=True)
                result = func(array=array, axis=axis)
                result.flags.writeable = False
                return result

            # this will be uninitialzied and thus, if a value is not assigned, will have garbage
            # dtype = None if not dtypes else dtypes[0]
            if dtypes:
                # Favor self._row_dtype's kind if it is in dtypes, else take first of passed dtypes
                for dt in dtypes:
                    if self._row_dtype.kind == dt.kind:
                        dtype = self._row_dtype
                        break
                else: # no break encountered
                    dtype = dtypes[0]
                astype_pre = dtype.kind in DTYPE_NAN_KIND
            else:
                dtype = self._row_dtype
                astype_pre = True # if no dtypes given (like bool) we can coerce

            # If dtypes werer specified, we know we have specific targets in mind for output

            out = np.empty(shape, dtype=dtype)
            # print('out', out, out.dtype, self._row_dtype)
            for idx, b in enumerate(self._blocks):

                if astype_pre and b.dtype != dtype:
                    # import ipdb; ipdb.set_trace()
                    b = b.astype(dtype)

                if axis == 0: # Combine rows, end with columns shape.
                    if b.size == 1 and size_one_unity and not skipna:
                        # No function call is necessary; if skipna could turn NaN to zero.
                        end = pos + 1
                        # Can assign an array, even 2D, as an element if size is 1
                        out[pos] = b
                    elif b.ndim == 1:
                        end = pos + 1
                        out[pos] = func(array=b, axis=axis)
                    else:
                        end = pos + b.shape[1]
                        func(array=b, axis=axis, out=out[pos: end])
                    pos = end
                else:
                    # Combine columns, end with block length shape and then call func again, for final result
                    if b.size == 1 and size_one_unity and not skipna:
                        out[:, idx] = b
                    elif b.ndim == 1:
                        # if this is a composable, numeric single columns we just copy it and process it later; but if this is a logical application (and, or) then it is already Boolean
                        if out.dtype == DTYPE_BOOL and b.dtype != DTYPE_BOOL:
                            # making 2D with axis 0 func will result in element-wise operation
                            out[:, idx] = func(array=column_2d_filter(b), axis=1)
                        else: # otherwise, keep as is
                            out[:, idx] = b
                    else:
                        func(array=b, axis=axis, out=out[:, idx])

        if axis == 0: # nothing more to do
            out.flags.writeable = False
            return out
        # If axis 1 and composable, can call function one more time on remaining components. Note that composability is problematic in cases where overflow is possible
        result = func(array=out, axis=1)
        result.flags.writeable = False
        return result


    #---------------------------------------------------------------------------
    def __len__(self) -> int:
        '''Length, as with NumPy and Pandas, is the number of rows. Note that A shape of (3, 0) will return a length of 3, even though there is no data.
        '''
        return self._shape[0]

    def display(self,
            config: tp.Optional[DisplayConfig] = None
            ) -> Display:
        '''
        Return a ``Display`` instance.
        '''
        config = config or DisplayActive.get()
        d = None
        outermost = True # only for the first
        idx = 0
        for block in self._blocks:
            block = column_2d_filter(block)
            if block.shape[1] == 0:
                continue

            h = '' if idx > 0 else self.__class__

            display = Display.from_values(block,
                    h,
                    config=config,
                    outermost=outermost)
            if not d: # assign first
                d = display
                outermost = False
            else:
                d.extend_display(display)

            # explicitly enumerate so as to not count no-width blocks
            idx += 1

        assert d is not None

        return d


    #---------------------------------------------------------------------------
    # extraction utilities

    @staticmethod
    def _cols_to_slice(indices: tp.Sequence[int]) -> slice:
        '''Translate an iterable of contiguous integers into a slice. Integers are assumed to be intentionally ordered and contiguous.
        '''
        start_idx = indices[0]
        # single column as a single slice
        if len(indices) == 1:
            return slice(start_idx, start_idx + 1)

        stop_idx = indices[-1]
        if stop_idx > start_idx: # ascending indices
            return slice(start_idx, stop_idx + 1)

        if stop_idx == 0:
            return slice(start_idx, None, -1)
        # stop is less than start, need to reduce by 1 to cover range
        return slice(start_idx, stop_idx - 1, -1)


    @classmethod
    def _indices_to_contiguous_pairs(cls, indices: tp.Iterable[tp.Tuple[int, int]]
        ) -> tp.Iterator[tp.Tuple[int, slice]]:
        '''Indices are pairs of (block_idx, value); convert these to pairs of (block_idx, slice) when we identify contiguous indices within a block (these are block slices)

        Args:
            indices: can be a generator
        '''
        # store pairs of block idx, ascending col list
        last = None
        for block_idx, col in indices:
            if not last:
                last = (block_idx, col)
                bundle = [col]
                continue
            if last[0] == block_idx and abs(col - last[1]) == 1:
                # if contiguous, update last, add to bundle
                last = (block_idx, col)
                # do not need to store all col, only the last, however probably easier to just accumulate all
                bundle.append(col)
                continue
            # either new block, or not contiguous on same block
            yield (last[0], cls._cols_to_slice(bundle))
            # start a new bundle
            bundle = [col]
            last = (block_idx, col)

        # last can be None
        if last and bundle:
            yield (last[0], cls._cols_to_slice(bundle))

    def _all_block_slices(self) -> tp.Iterator[tp.Tuple[int, slice]]:
        '''
        Alternaitve to _indices_to_contiguous_pairs when we need all indices per block in a slice.
        '''
        for idx, b in enumerate(self._blocks):
            if b.ndim == 1:
                yield (idx, UNIT_SLICE) # cannot give an integer here instead of a slice
            else:
                yield (idx, slice(0, b.shape[1]))

    # @profile
    def _key_to_block_slices(self,
            key: GetItemKeyTypeCompound,
            retain_key_order: bool = True
            ) -> tp.Iterator[tp.Tuple[int, tp.Union[slice, int]]]:
        '''
        For a column key (an integer, slice, or iterable), generate pairs of (block_idx, slice or integer) to cover all extractions. First, get the relevant index values (pairs of block id, column id), then convert those to contiguous slices.

        Args:
            retain_key_order: if False, returned slices will be in ascending order.

        Returns:
            A generator iterable of pairs, where values are block index, slice or column index
        '''
        if key is None or (isinstance(key, slice) and key == NULL_SLICE):
            yield from self._all_block_slices() # slow from line profiler, 80% of this function call

        else:
            if isinstance(key, INT_TYPES):
                # the index has the pair block, column integer
                yield self._index[key]
            else: # all cases where we try to get contiguous slices
                if isinstance(key, slice):
                    #  slice the index; null slice already handled
                    if not retain_key_order:
                        key = slice_to_ascending_slice(key, self._shape[1])
                    indices: tp.Iterable[tp.Tuple[int, int]] = self._index[key]
                elif isinstance(key, np.ndarray) and key.dtype == bool:
                    # NOTE: if self._index was an array we could use Boolean selection directly
                    indices = (self._index[idx] for idx, v in enumerate(key) if v)
                elif isinstance(key, KEY_ITERABLE_TYPES):
                    # an iterable of keys, may not have contiguous regions; provide in the order given; set as a generator; self._index is a list, not an np.array, so cannot slice self._index; requires iteration in passed generator so probably this is as fast as it can be.
                    if retain_key_order:
                        indices = (self._index[x] for x in key)
                    else:
                        indices = (self._index[x] for x in sorted(key))
                elif key is None: # get all
                    indices = self._index
                else:
                    raise NotImplementedError('Cannot handle key', key)
                yield from self._indices_to_contiguous_pairs(indices)


    #---------------------------------------------------------------------------
    def _mask_blocks(self,
            row_key: tp.Optional[GetItemKeyTypeCompound] = None,
            column_key: tp.Optional[GetItemKeyTypeCompound] = None) -> tp.Iterator[np.ndarray]:
        '''Return Boolean blocks of the same size and shape, where key selection sets values to True.
        '''

        # this selects the columns; but need to return all blocks

        # block slices must be in ascending order, not key order
        block_slices = iter(self._key_to_block_slices(
                column_key,
                retain_key_order=False))
        target_block_idx = target_slice = None
        targets_remain = True

        for block_idx, b in enumerate(self._blocks):
            mask = np.full(b.shape, False, dtype=bool)

            while targets_remain:
                # get target block and slice
                if target_block_idx is None: # can be zero
                    try:
                        target_block_idx, target_slice = next(block_slices)
                    except StopIteration:
                        targets_remain = False
                        break

                if block_idx != target_block_idx:
                    break # need to advance blocks

                if b.ndim == 1: # given 1D array, our row key is all we need
                    mask[row_key] = True
                else:
                    if row_key is None:
                        mask[:, target_slice] = True
                    else:
                        mask[row_key, target_slice] = True

                target_block_idx = target_slice = None

            yield mask


    def _astype_blocks(self,
            column_key: GetItemKeyType,
            dtype: DtypeSpecifier
            ) -> tp.Generator[np.ndarray, None, None]:
        '''
        Generator producer of np.ndarray.
        '''
        # block slices must be in ascending order, not key order
        block_slices = iter(self._key_to_block_slices(
                column_key,
                retain_key_order=False))

        target_slice: tp.Optional[tp.Union[slice, int]]

        target_block_idx = target_slice = None
        targets_remain = True

        for block_idx, b in enumerate(self._blocks):
            parts = []
            part_start_last = 0

            while targets_remain:
                # get target block and slice
                if target_block_idx is None: # can be zero
                    try:
                        target_block_idx, target_slice = next(block_slices)
                    except StopIteration:
                        targets_remain = False
                        break

                if block_idx != target_block_idx:
                    break # need to advance blocks

                if dtype == b.dtype:
                    target_block_idx = target_slice = None
                    continue # there may be more slices for this block

                if b.ndim == 1: # given 1D array, our row key is all we need
                    parts.append(b.astype(dtype))
                    part_start_last = 1
                    target_block_idx = target_slice = None
                    break

                assert target_slice is not None
                # target_slice can be a slice or an integer
                if isinstance(target_slice, slice):
                    target_start = target_slice.start
                    target_stop = target_slice.stop
                else: # it is an integer
                    target_start = target_slice
                    target_stop = target_slice + 1

                assert target_start is not None and target_stop is not None
                if target_start > part_start_last:
                    # yield un changed components before and after
                    parts.append(b[:, slice(part_start_last, target_start)])

                parts.append(b[:, target_slice].astype(dtype))
                part_start_last = target_stop

                target_block_idx = target_slice = None

            # if this is a 1D block, we either convert it or do not, and thus either have parts or not, and do not need to get other part pieces of the block
            if b.ndim != 1 and part_start_last < b.shape[1]:
                parts.append(b[:, slice(part_start_last, None)])

            if not parts:
                yield b # no change for this block
            else:
                yield from parts


    def _drop_blocks(self,
            row_key: GetItemKeyType = None,
            column_key: GetItemKeyType = None,
            ) -> tp.Generator[np.ndarray, None, None]:
        '''
        Generator producer of np.ndarray. Note that this appraoch should be more efficient than using selection/extraction, as here we are only concerned with columns.

        Args:
            column_key: Selection of columns to leave out of blocks.
        '''
        if column_key is None:
            # the default should not be the null slice, which would drop all
            block_slices: tp.Iterator[tp.Tuple[int, tp.Union[slice, int]]] = iter(())
        else:
            if not self._blocks:
                raise IndexError(f'cannot drop columns from zero-blocks')
            # block slices must be in ascending order, not key order
            block_slices = iter(self._key_to_block_slices(
                    column_key,
                    retain_key_order=False))

        if isinstance(row_key, np.ndarray) and row_key.dtype == bool:
            # row_key is used with np.delete, which does not support Boolean arrays; instead, convert to an array of integers
            row_key = np.arange(len(row_key))[row_key]

        target_block_idx = target_slice = None
        targets_remain = True

        for block_idx, b in enumerate(self._blocks):
            # for each block, we evaluate if we have any targets in that block and update the block accordingly; otherwise, we yield the block unchanged

            parts = []
            drop_block = False # indicate entire block is dropped
            part_start_last = 0 # within this block, keep track of where our last change was started

            while targets_remain:
                # get target block and slice; this is what we want to remove
                if target_block_idx is None: # can be zero
                    try:
                        target_block_idx, target_slice = next(block_slices)
                    except StopIteration:
                        targets_remain = False
                        break

                if block_idx != target_block_idx:
                    break # need to advance blocks

                if b.ndim == 1 or b.shape[1] == 1: # given 1D array or 2D, 1 col array
                    part_start_last = 1
                    target_block_idx = target_slice = None
                    drop_block = True
                    break

                # target_slice can be a slice or an integer
                if isinstance(target_slice, slice):
                    target_start = target_slice.start
                    target_stop = target_slice.stop
                else: # it is an integer
                    target_start = target_slice # can be zero
                    target_stop = target_slice + 1

                assert target_start is not None and target_stop is not None
                # if the target start (what we want to remove) is greater than 0 or our last starting point, then we need to slice off everything that came before, so as to keep it
                if target_start == 0 and target_stop == b.shape[1]:
                    drop_block = True
                elif target_start > part_start_last:
                    # yield retained components before and after
                    parts.append(b[:, slice(part_start_last, target_start)])
                part_start_last = target_stop
                # reset target block index, forcing fetchin next target info
                target_block_idx = target_slice = None

            # if this is a 1D block we can rely on drop_block Boolean and parts list to determine action
            if b.ndim != 1 and 0 < part_start_last < b.shape[1]:
                # if a 2D block, and part_start_last is less than the shape, collect the remaining slice
                parts.append(b[:, slice(part_start_last, None)])

            # for row deletions, we use np.delete, which handles finding the inverse of a slice correctly; the returned array requires writeability re-set; np.delete does not work correctly with Boolean selectors
            if not drop_block and not parts:
                if row_key is not None:
                    b = np.delete(b, row_key, axis=0)
                    b.flags.writeable = False
                yield b
            elif parts:
                if row_key is not None:
                    for part in parts:
                        part = np.delete(part, row_key, axis=0)
                        part.flags.writeable = False
                        yield part
                else:
                    yield from parts


    def _shift_blocks(self,
            row_shift: int = 0,
            column_shift: int = 0,
            wrap: bool = True,
            fill_value: object = np.nan
            ) -> tp.Generator[np.ndarray, None, None]:
        '''
        Shift type blocks independently on rows or columns. When ``wrap`` is True, the operation is a roll-style shift; when ``wrap`` is False, shifted-out values are not replaced and are filled with ``fill_value``.
        '''
        row_count, column_count = self._shape

        # new start index is the opposite of the shift; if shifting by 2, the new start is the second from the end
        index_start_pos = -(column_shift % column_count)
        row_start_pos = -(row_shift % row_count)

        # possibly be truthy
        # index is columns here
        if wrap and index_start_pos == 0 and row_start_pos == 0:
            yield from self._blocks
        if not wrap and column_shift == 0 and row_shift == 0:
            yield from self._blocks
        else:
            block_start_idx, block_start_column = self._index[index_start_pos]
            block_start = self._blocks[block_start_idx]

            if block_start_column == 0:
                # we are starting at the block, no tail, always yield;  captures all 1 dim block cases
                block_head_iter: tp.Iterable[np.ndarray] = chain(
                        (block_start,),
                        self._blocks[block_start_idx + 1:])
                block_tail_iter: tp.Iterable[np.ndarray] = self._blocks[:block_start_idx]
            else:
                block_head_iter = chain(
                        (block_start[:, block_start_column:],),
                        self._blocks[block_start_idx + 1:])
                block_tail_iter = chain(
                        self._blocks[:block_start_idx],
                        (block_start[:, :block_start_column],)
                        )

            if not wrap:
                shape = (self._shape[0], min(self._shape[1], abs(column_shift)))
                empty = np.full(shape, fill_value)
                if column_shift > 0:
                    block_head_iter = (empty,)
                elif column_shift < 0:
                    block_tail_iter = (empty,)

            # NOTE: might consider not rolling when yielding an empty array
            for b in chain(block_head_iter, block_tail_iter):
                if (wrap and row_start_pos == 0) or (not wrap and row_shift == 0):
                    yield b
                else:
                    b = array_shift(
                            array=b,
                            shift=row_shift,
                            axis=0,
                            wrap=wrap,
                            fill_value=fill_value)
                    b.flags.writeable = False
                    yield b


    def _assign_blocks_from_keys(self,
            row_key: tp.Optional[GetItemKeyTypeCompound] = None,
            column_key: tp.Optional[GetItemKeyTypeCompound] = None,
            value: object = None
            ) -> tp.Iterator[np.ndarray]:
        '''Assign value into all blocks, returning blocks of the same size and shape.
        '''
        if isinstance(value, np.ndarray):
            value_dtype = value.dtype
        else:
            value_dtype = np.array(value).dtype

        # this selects the columns; but need to return all blocks
        block_slices = iter(self._key_to_block_slices(column_key))
        target_block_idx = target_slice = None
        targets_remain = True

        for block_idx, b in enumerate(self._blocks):

            assigned = None
            while targets_remain:
                if target_block_idx is None: # can be zero
                    try:
                        target_block_idx, target_slice = next(block_slices)
                    except StopIteration:
                        targets_remain = False
                        break

                if block_idx != target_block_idx:
                    break # need to advance blocks, keep targets

                # from here, we have a target we need to apply
                if assigned is None:
                    assigned_dtype = resolve_dtype(value_dtype, b.dtype)
                    if b.dtype == assigned_dtype:
                        assigned = b.copy()
                    else:
                        assigned = b.astype(assigned_dtype)

                # match sliceable, when target_slice is a slice (can be an integer)
                if (isinstance(target_slice, slice) and
                        not isinstance(value, str)
                        and hasattr(value, '__len__')):
                    if b.ndim == 1:
                        width = 1
                        # if block is 1D, then we can only take 1 column if we have a 2d value
                        value_piece_column_key: tp.Union[slice, int] = 0
                    else:
                        width = len(range(*target_slice.indices(assigned.shape[1])))
                        # if block id 2D, can take up to width from value
                        value_piece_column_key = slice(0, width)

                    if isinstance(value, np.ndarray) and value.ndim > 1:
                        # if value is 2D array, we want value[:, 0]
                        value_piece = value[:, value_piece_column_key]
                        value = value[:, slice(width, None)]
                        # reassign remainder for next iteration
                    else: # value is 1D array or tuple
                        # we assume we assigning into a horizontal position
                        value_piece = value[value_piece_column_key]
                        value = value[slice(width, None)]
                else: # not sliceable; this can be a single column
                    value_piece = value

                if b.ndim == 1: # given 1D array, our row key is all we need
                    # TODO: handle row_key of None
                    assigned[row_key] = value_piece
                else:
                    if row_key is None:
                        assigned[:, target_slice] = value_piece
                    else:
                        assigned[row_key, target_slice] = value_piece

                target_block_idx = target_slice = None

            if assigned is None:
                yield b # no change
            else:
                # disable writing so clients can keep the array
                assigned.flags.writeable = False
                yield assigned


    def _assign_blocks_from_boolean_blocks(self,
            targets: tp.Iterable[np.ndarray],
            value: object
            ) -> tp.Iterator[np.ndarray]:
        '''Assign value into all blocks based on a Bolean arrays of shape equal to each block in these blocks, returning blocks of the same size and shape. Value is set where the Boolean is True.

        Args:
            value: Must be a single value, rather than an array
        '''
        if isinstance(value, np.ndarray):
            raise Exception('cannot assign an array with Boolean targets')
        else:
            value_dtype = np.array(value).dtype

        for block, target in zip_longest(self._blocks, targets):
            if block is None or target is None:
                raise Exception('blocks or targets do not align')

            if not target.any():
                yield block
            else:
                assigned_dtype = resolve_dtype(value_dtype, block.dtype)
                if block.dtype == assigned_dtype:
                    assigned = block.copy()
                else:
                    assigned = block.astype(assigned_dtype)

                # assert assigned.shape == target.shape
                assigned[target] = value
                assigned.flags.writeable = False
                yield assigned


    def _assign_blocks_from_bloc_key(self,
            bloc_key: np.ndarray,
            value: tp.Any # an array, or element for single assigment
            ) -> tp.Iterator[np.ndarray]:
        '''
        Given an Boolean array of targets, fill targets from value, where value is either a single value or an array.
        '''

        if isinstance(value, np.ndarray):
            value_dtype = value.dtype
            is_element = False
            assert value.shape == self.shape
        else:
            value_dtype = np.array(value).dtype
            is_element = True

        start = 0
        target_slice: tp.Union[int, slice]

        for block in self._blocks:

            if block.ndim == 1:
                end = start + 1
                target_slice = start
            else:
                end = start + block.shape[1]
                target_slice = slice(start, end)

            target = bloc_key[NULL_SLICE, target_slice]

            if not target.any():
                yield block
            else:
                assigned_dtype = resolve_dtype(value_dtype, block.dtype)
                if block.dtype == assigned_dtype:
                    assigned = block.copy()
                else:
                    assigned = block.astype(assigned_dtype)

                assert assigned.shape == target.shape

                if is_element:
                    assigned[target] = value
                else:
                    assigned[target] = value[NULL_SLICE, target_slice][target]

                assigned.flags.writeable = False
                yield assigned

            start = end # always update start

    def _slice_blocks(self,
            row_key: tp.Optional[GetItemKeyTypeCompound] = None,
            column_key: tp.Optional[GetItemKeyTypeCompound] = None) -> tp.Iterator[np.ndarray]:
        '''
        Generator of sliced blocks, given row and column key selectors.
        The result is suitable for passing to TypeBlocks constructor.
        '''
        row_key_null = (row_key is None or
                (isinstance(row_key, slice) and row_key == NULL_SLICE))

        single_row = False
        if row_key_null:
            if self._shape[0] == 1:
                # this codition used to only hold if the arg is a null slice; now if None too and shape has one row
                single_row = True
        elif isinstance(row_key, INT_TYPES):
            single_row = True
        elif isinstance(row_key, KEY_ITERABLE_TYPES) and len(row_key) == 1:
            # an iterable of index integers is expected here
            single_row = True
        elif isinstance(row_key, slice):
            # need to determine if there is only one index returned by range (after getting indices from the slice); do this without creating a list/tuple, or walking through the entire range; get constant time look-up of range length after uses slice.indicies
            if len(range(*row_key.indices(self._shape[0]))) == 1:
                single_row = True
        elif isinstance(row_key, np.ndarray) and row_key.dtype == bool:
            # TODO: need fastest way to find if there is more than one boolean
            if row_key.sum() == 1:
                single_row = True

        # convert column_key into a series of block slices; we have to do this as we stride blocks; do not have to convert row_key as can use directly per block slice
        for block_idx, slc in self._key_to_block_slices(column_key): # slow from line profiler
            b = self._blocks[block_idx]
            if b.ndim == 1: # given 1D array, our row key is all we need
                if row_key_null:
                    block_sliced = b
                else:
                    block_sliced = b[row_key] # slow from line profiler
            else: # given 2D, use row key and column slice
                if row_key_null:
                    block_sliced = b[:, slc]
                else:
                    block_sliced = b[row_key, slc]

            # optionally, apply additional selection, reshaping, or adjustments to what we got out of the block
            if isinstance(block_sliced, np.ndarray):
                # if we have a single row and the thing we sliced is 1d, we need to rotate it
                if single_row and block_sliced.ndim == 1:
                    block_sliced = block_sliced.reshape(1, block_sliced.shape[0])
                # if we have a single column as 2d, unpack it; however, we have to make sure this is not a single row in a 2d
                elif (block_sliced.ndim == 2
                        and block_sliced.shape[0] == 1
                        and not single_row):
                    block_sliced = block_sliced[0]
            else: # a single element, wrap back up in array
                block_sliced = np.array((block_sliced,), dtype=b.dtype)

            yield block_sliced


    def _extract_array(self,
            row_key: tp.Optional[GetItemKeyTypeCompound] = None,
            column_key: tp.Optional[GetItemKeyTypeCompound] = None) -> np.ndarray:
        '''Alternative extractor that returns just an np array, concatenating blocks as necessary. Used by internal clients that need to process row/column with an array.

        This will be consistent with NumPy as to the dimensionality returned: if a non-multi selection is made, 1D array will be returned.
        '''
        # identifying column_key as integer, then we only access one block, and can return directly without iterating over blocks
        if isinstance(column_key, INT_TYPES):
            block_idx, column = self._index[column_key]
            b = self._blocks[block_idx]
            if b.ndim == 1:
                if row_key is None:
                    return b
                return b[row_key]
            if row_key is None:
                return b[:, column]
            return b[row_key, column]

        # figure out shape from keys so as to not accumulate?
        blocks = []
        rows = 0
        columns = 0
        for b in tuple(self._slice_blocks( # a generator
                row_key=row_key,
                column_key=column_key)):
            if b.ndim == 1: # it is a single column
                if not rows: # assume all the same after first
                    # if 1d, then the length should be the number of rows
                    rows = b.shape[0]
                columns += 1
            else:
                if not rows: # assume all the same after first
                    rows = b.shape[0]
                columns += b.shape[1]
            blocks.append(b)

        row_dtype = resolve_dtype_iter(b.dtype for b in blocks)
        row_multiple = row_key is None or isinstance(row_key, KEY_MULTIPLE_TYPES)

        return self._blocks_to_array(
                blocks=blocks,
                shape=(rows, columns),
                row_dtype=row_dtype,
                row_multiple=row_multiple)

    def _extract(self,
            row_key: GetItemKeyType = None,
            column_key: GetItemKeyType = None) -> tp.Union['TypeBlocks', np.ndarray]: # but sometimes an element
        '''
        Return a TypeBlocks after performing row and column selection using iloc selection.

        Row and column keys can be:
            integer: single row/column selection
            slices: one or more contiguous selections
            iterable of integers: one or more non-contiguous and/or repeated selections

        Note: Boolean-based selection is not (yet?) implemented here, but instead will be implemented at the `loc` level. This might imply that Boolean selection is only available with `loc`.

        Returns:
            TypeBlocks, or a single element if both are coordinats
        '''
        # identifying column_key as integer, then we only access one block, and can return directly without iterating over blocks
        if isinstance(column_key, INT_TYPES):
            block_idx, column = self._index[column_key]
            b = self._blocks[block_idx]
            row_key_null = (row_key is None or
                    (isinstance(row_key, slice)
                    and row_key == NULL_SLICE))
            if b.ndim == 1:
                if row_key_null: # return a column
                    return TypeBlocks.from_blocks(b)
                elif isinstance(row_key, int):
                    return b[row_key] # return single item
                return TypeBlocks.from_blocks(b[row_key])

            if row_key_null:
                return TypeBlocks.from_blocks(b[:, column])
            elif isinstance(row_key, int):
                return b[row_key, column] # return single item
            return TypeBlocks.from_blocks(b[row_key, column])

        # pass a generator to from_block; will return a TypeBlocks or a single element
        return self.from_blocks(
                self._slice_blocks(
                        row_key=row_key,
                        column_key=column_key),
                shape_reference=self._shape
                )


    def _extract_iloc(self,
            key: GetItemKeyTypeCompound
            ) -> 'TypeBlocks':
        if isinstance(key, tuple):
            return self._extract(*key)
        return self._extract(row_key=key)

    def extract_iloc_mask(self,
            key: GetItemKeyTypeCompound
            ) -> 'TypeBlocks':
        if isinstance(key, tuple):
            return TypeBlocks.from_blocks(self._mask_blocks(*key))
        return TypeBlocks.from_blocks(self._mask_blocks(row_key=key))

    def extract_iloc_assign(self,
            key: GetItemKeyTypeCompound,
            value: object
            ) -> 'TypeBlocks':
        if isinstance(key, tuple):
            key = tp.cast(tp.Tuple[int, int], key)
            return TypeBlocks.from_blocks(self._assign_blocks_from_keys(*key, value=value))
        return TypeBlocks.from_blocks(self._assign_blocks_from_keys(row_key=key, value=value))

    def extract_bloc_assign(self,
            key: np.ndarray,
            value: tp.Any
            ) -> 'TypeBlocks':
        return TypeBlocks.from_blocks(self._assign_blocks_from_bloc_key(
                bloc_key=key,
                value=value
                ))


    def drop(self, key: GetItemKeyTypeCompound) -> 'TypeBlocks':
        '''
        Drop rows or columns from a TyepBlocks instance.

        Args:
            key: if a single value, treated as a row key; if a tuple, treated as a pair of row, column keys.
        '''
        if isinstance(key, tuple):
            # column dropping can leed to a TB with generator that yields nothing;
            return TypeBlocks.from_blocks(
                    self._drop_blocks(*key),
                    shape_reference=self._shape
                    )
        return TypeBlocks.from_blocks(
                self._drop_blocks(row_key=key),
                shape_reference=self._shape
                )


    def __getitem__(self, key: GetItemKeyTypeCompound) -> 'TypeBlocks':
        '''
        Returns a column, or a column slice.
        '''
        # NOTE: if key is a tuple it means that multiple indices are being provided; this should probably raise an error
        if isinstance(key, tuple):
            raise KeyError('__getitem__ does not support multiple indexers')
        return self._extract(row_key=None, column_key=key)

    #---------------------------------------------------------------------------
    # operators

    def _ufunc_unary_operator(self, operator: tp.Callable[[np.ndarray], np.ndarray]) -> 'TypeBlocks':
        # for now, do no reblocking; though, in many cases, operating on a unified block will be faster
        def operation() -> tp.Iterator[np.ndarray]:
            for b in self._blocks:
                result = operator(b)
                result.flags.writeable = False
                yield result

        return self.from_blocks(operation())

    #---------------------------------------------------------------------------

    def _block_shape_slices(self) -> tp.Iterator[slice]:
        '''Generator of slices necessary to slice a 1d array of length equal to the number of columns into a lenght suitable for each block.
        '''
        start = 0
        for b in self._blocks:
            end = start + (1 if b.ndim == 1 else b.shape[1])
            yield slice(start, end)
            start = end

    def _ufunc_binary_operator(self, *,
            operator: tp.Callable[[np.ndarray, np.ndarray], np.ndarray],
            other: tp.Iterable[tp.Any]
            ) -> 'TypeBlocks':

        if operator.__name__ == 'matmul' or operator.__name__ == 'rmatmul':
            # this could be implemented but would force block consolidation
            raise NotImplementedError('matrix multiplication not supported')

        if isinstance(other, TypeBlocks):
            if self.block_compatible(other, axis=None):
                # this means that the blocks are the same shape; we do not check types
                self_operands = self._blocks
                other_operands = other._blocks
            elif self._shape == other._shape:
                # if the result of reblock does not result in compatible shapes, we have to use .values as operands; the dtypes can be different so we only have to check that they columns sizes, the second element of the signature, all match.
                if not self.reblock_compatible(other):
                    self_operands = (self.values,)
                    other_operands = (other.values,)
                else:
                    self_operands = self._reblock()
                    other_operands = other._reblock()
            else: # raise same error as NP
                raise NotImplementedError('cannot apply binary operators to arbitrary TypeBlocks')

            def operation() -> tp.Iterator[np.ndarray]:
                for a, b in zip_longest(
                        (column_2d_filter(op) for op in self_operands),
                        (column_2d_filter(op) for op in other_operands)
                        ):
                    result = operator(a, b)
                    result.flags.writeable = False # own the data
                    yield result
        else:
            # process other as an array
            self_operands = self._blocks
            if not isinstance(other, np.ndarray):
                # this maybe expensive for a single scalar
                other = np.array(other) # this will work with a single scalar too

            # handle dimensions
            if other.ndim == 0 or (other.ndim == 1 and len(other) == 1):
                # a scalar: reference same value for each block position
                other_operands = (other for _ in range(len(self._blocks)))
            elif other.ndim == 1 and len(other) == self._shape[1]:
                # if given a 1d array
                # one dimensional array of same size: chop to block width
                other_operands = (other[s] for s in self._block_shape_slices())
            else:
                raise NotImplementedError('cannot apply binary operators to arbitrary np arrays.')

            def operation() -> tp.Iterator[np.ndarray]:
                for a, b in zip_longest(self_operands, other_operands):
                    result = operator(a, b)
                    result.flags.writeable = False # own the data
                    yield result

        return self.from_blocks(operation())



    def _ufunc_axis_skipna(self, *,
            axis: int,
            skipna: bool,
            ufunc: UFunc,
            ufunc_skipna: UFunc,
            composable: bool,
            dtypes: tp.Tuple[np.dtype, ...],
            size_one_unity: bool
            ) -> np.ndarray:
        # not sure if these make sense on TypeBlocks, as they reduce dimensionality
        raise NotImplementedError()

    def _ufunc_shape_skipna(self, *,
            axis: int,
            skipna: bool,
            ufunc: UFunc,
            ufunc_skipna: UFunc,
            composable: bool,
            dtypes: tp.Tuple[np.dtype, ...],
            size_one_unity: bool
            ) -> np.ndarray:
        # not sure if these make sense on TypeBlocks, as they reduce dimensionality
        raise NotImplementedError()


    #---------------------------------------------------------------------------
    # transformations resulting in the same dimensionality

    def transpose(self) -> 'TypeBlocks':
        '''Return a new TypeBlocks that transposes and concatenates all blocks.
        '''
        blocks = []
        for b in self._blocks:
            b = column_2d_filter(b).transpose()
            if b.dtype != self._row_dtype:
                b = b.astype(self._row_dtype)
            blocks.append(b)
        a = np.concatenate(blocks)
        a.flags.writeable = False # keep this array
        return self.from_blocks(a)


    def isna(self) -> 'TypeBlocks':
        '''Return a Boolean TypeBlocks where True is NaN or None.
        '''
        def blocks() -> tp.Iterator[np.ndarray]:
            for b in self._blocks:
                bool_block = isna_array(b)
                bool_block.flags.writeable = False
                yield bool_block

        return self.from_blocks(blocks())


    def notna(self) -> 'TypeBlocks':
        '''Return a Boolean TypeBlocks where True is not NaN or None.
        '''
        def blocks() -> tp.Iterator[np.ndarray]:
            for b in self._blocks:
                bool_block = np.logical_not(isna_array(b))
                bool_block.flags.writeable = False
                yield bool_block

        return self.from_blocks(blocks())

    #---------------------------------------------------------------------------
    # fillna sided

    @staticmethod
    def _fillna_sided_axis_0(
            blocks: tp.Iterable[np.ndarray],
            value: tp.Any,
            sided_leading: bool) -> tp.Iterator[np.ndarray]:
        '''Return a TypeBlocks where NaN or None are replaced in sided (leading or trailing) segments along axis 0, meaning vertically.

        Args:
            sided_leading: True sets the side to fill is the leading side; False sets the side to fill to the trailiing side.

        '''
        if isinstance(value, np.ndarray):
            raise RuntimeError('cannot assign an array to fillna')

        sided_index = 0 if sided_leading else -1

        # store flag for when non longer need to check blocks, yield immediately

        for b in blocks:
            sel = isna_array(b) # True for is NaN
            ndim = sel.ndim

            if ndim == 1 and not sel[sided_index]:
                # if last value (bottom row) is not NaN, we can return block
                yield b
            elif ndim > 1 and ~sel[sided_index].any(): # if not any are NaN
                # can use this last-row observation below
                yield b
            else:
                assignable_dtype = resolve_dtype(np.array(value).dtype, b.dtype)
                if b.dtype == assignable_dtype:
                    assigned = b.copy()
                else:
                    assigned = b.astype(assignable_dtype)

                # because np.nonzero is easier / faster to parse if applied on a 1D array, w can make 2d look like 1D here
                if ndim == 1:
                    sel_nonzeros = ((0, sel),)
                else:
                    # only collect columns for sided NaNs
                    sel_nonzeros = ((i, sel[:, i]) for i, j in enumerate(sel[sided_index]) if j)

                for idx, sel_nonzero in sel_nonzeros:
                    # indices of not-nan values, per column
                    targets = np.nonzero(~sel_nonzero)[0]
                    if len(targets):
                        if sided_leading:
                            sel_slice = slice(0, targets[0])
                        else: # trailings
                            sel_slice = slice(targets[-1]+1, None)
                    else: # all are NaN
                        sel_slice = NULL_SLICE

                    if ndim == 1:
                        assigned[sel_slice] = value
                    else:
                        assigned[sel_slice, idx] = value

                # done writing
                assigned.flags.writeable = False
                yield assigned


    @staticmethod
    def _fillna_sided_axis_1(
            blocks: tp.Iterable[np.ndarray],
            value: tp.Any,
            sided_leading: bool) -> tp.Iterator[np.ndarray]:
        '''Return a TypeBlocks where NaN or None are replaced in sided (leading or trailing) segments along axis 1.

        NOTE: blocks are generated in reverse order when sided_leading is False.

        Args:
            sided_leading: True sets the side to fill is the leading side; False sets the side to fill to the trailiing side.

        '''
        if isinstance(value, np.ndarray):
            raise RuntimeError('cannot assign an array to fillna')

        sided_index = 0 if sided_leading else -1

        # will need to re-reverse blocks coming out of this
        block_iter = blocks if sided_leading else reversed(blocks)

        isna_exit_previous = None

        # iterate over blocks to observe NaNs contiguous horizontally
        for b in block_iter:
            sel = isna_array(b) # True for is NaN
            ndim = sel.ndim

            if isna_exit_previous is None:
                # for first block, model as all True
                isna_exit_previous = np.full(sel.shape[0], True, dtype=bool)

            # to contunue nan propagation, the exit previous musy be NaN, as well as this start
            if ndim == 1:
                isna_entry = sel & isna_exit_previous
            else:
                isna_entry = sel[:, sided_index] & isna_exit_previous

            if not isna_entry.any():
                yield b
            else:
                assignable_dtype = resolve_dtype(np.array(value).dtype, b.dtype)
                if b.dtype == assignable_dtype:
                    assigned = b.copy()
                else:
                    assigned = b.astype(assignable_dtype)

                if ndim == 1:
                    # if one dim, we simply fill nan values
                    assigned[isna_entry] = value
                else:
                    # only collect rows that have a sided NaN
                    # could use np.nonzero()
                    candidates = (i for i, j in enumerate(isna_entry) if j == True)
                    sels_nonzero = ((i, sel[i]) for i in candidates)

                    for idx, sel_nonzero in sels_nonzero:
                        # indices of not-nan values, per row
                        targets = np.nonzero(~sel_nonzero)[0]
                        if len(targets):
                            if sided_leading:
                                sel_slice = slice(0, targets[0])
                            else: # trailing
                                sel_slice = slice(targets[-1]+1, None)
                        else: # all are NaN
                            sel_slice = NULL_SLICE

                        if ndim == 1:
                            assigned[sel_slice] = value
                        else:
                            assigned[idx, sel_slice] = value

                assigned.flags.writeable = False
                yield assigned

            # always execute these lines after each yield
            # return True for next block only if all values are NaN in the row
            if ndim == 1:
                isna_exit_previous = isna_entry
            else:
                isna_exit_previous = sel.all(axis=1) & isna_exit_previous


    def fillna_leading(self,
            value: tp.Any,
            *,
            axis: int = 0) -> 'TypeBlocks':
        '''Return a TypeBlocks instance replacing leading values with the passed `value`. Leading, axis 0 fills columns, going from top to bottom. Leading axis 1 fills rows, going from left to right.
        '''
        if axis == 0:
            return self.from_blocks(self._fillna_sided_axis_0(
                    blocks=self._blocks,
                    value=value,
                    sided_leading=True))
        elif axis == 1:
            return self.from_blocks(self._fillna_sided_axis_1(
                    blocks=self._blocks,
                    value=value,
                    sided_leading=True))
        raise NotImplementedError(f'no support for axis {axis}')

    def fillna_trailing(self,
            value: tp.Any,
            *,
            axis: int = 0) -> 'TypeBlocks':
        '''Return a TypeBlocks instance replacing trailing NaNs with the passed `value`. Trailing, axis 0 fills columns, going from bottom to top. Trailing axis 1 fills rows, going from right to left.
        '''
        if axis == 0:
            return self.from_blocks(self._fillna_sided_axis_0(
                    blocks=self._blocks,
                    value=value,
                    sided_leading=False))
        elif axis == 1:
            # must reverse when not leading
            blocks = reversed(tuple(self._fillna_sided_axis_1(
                    blocks=self._blocks,
                    value=value,
                    sided_leading=False)))
            return self.from_blocks(blocks)

        raise NotImplementedError(f'no support for axis {axis}')

    #---------------------------------------------------------------------------
    # fillna directional

    @staticmethod
    def _fillna_directional_axis_0(
            blocks: tp.Iterable[np.ndarray],
            directional_forward: bool,
            limit: int = 0
            ) -> tp.Iterator[np.ndarray]:
        '''
        Do a directional fill along axis 0, meaning filling vertically, going top/bottom or bottom/top.

        Args:
            directional_forward: if True, start from the forward (top or left) side.
        '''

        for b in blocks:
            sel = isna_array(b) # True for is NaN
            ndim = sel.ndim

            if ndim == 1 and not np.any(sel):
                yield b
            elif ndim == 2 and not np.any(sel).any():
                yield b
            else:
                target_indexes = binary_transition(sel)

                if ndim == 1:
                    # make single array look like iterable of tuples
                    slots = 1
                    length = len(sel)

                elif ndim == 2:
                    slots = b.shape[1] # axis 0 has column width
                    length = b.shape[0]

                # type is already compatible, no need for check
                assigned = b.copy()

                for i in range(slots):

                    if ndim == 1:
                        target_index = target_indexes
                        if not len(target_index):
                            continue
                        target_values = b[target_index]

                        def slice_condition(target_slice: slice) -> bool:
                            return sel[target_slice][0] # type: ignore

                    else:
                        target_index = target_indexes[i]
                        if not target_index:
                            continue
                        target_values = b[target_index, i]

                        def slice_condition(target_slice: slice) -> bool:
                            return sel[target_slice, i][0] # type: ignore

                    for target_slice, value in slices_from_targets(
                            target_index=target_index,
                            target_values=target_values,
                            length=length,
                            directional_forward=directional_forward,
                            limit=limit,
                            slice_condition=slice_condition
                            ):

                        if ndim == 1:
                            assigned[target_slice] = value
                        else:
                            assigned[target_slice, i] = value

                assigned.flags.writeable = False
                yield assigned



    @staticmethod
    def _fillna_directional_axis_1(
            blocks: tp.Iterable[np.ndarray],
            directional_forward: bool,
            limit: int = 0
            ) -> tp.Iterator[np.ndarray]:
        '''
        Do a directional fill along axis 1, or horizontally, going left to right or right to left.

        NOTE: blocks are generated in reverse order when directional_forward is False.

        '''
        bridge_src_index = -1 if directional_forward else 0
        bridge_dst_index = 0 if directional_forward else -1

        # will need to re-reverse blocks coming out of this
        block_iter = blocks if directional_forward else reversed(blocks) # type: ignore

        bridging_values: tp.Optional[np.ndarray] = None
        bridging_count: tp.Optional[np.ndarray] = None
        bridging_isna: tp.Optional[np.ndarray] = None # Boolean array describing isna of bridging values

        for b in block_iter:
            sel = isna_array(b) # True for is NaN
            ndim = sel.ndim

            if ndim == 1 and not np.any(sel):
                bridging_values = b
                bridging_isna = sel
                bridging_count = np.full(b.shape[0], 0)
                yield b
            elif ndim == 2 and not np.any(sel).any():
                bridging_values = b[:, bridge_src_index]
                bridging_isna = sel[:, bridge_src_index]
                bridging_count = np.full(b.shape[0], 0)
                yield b
            else: # some NA in this block
                if bridging_values is None:
                    assigned = b.copy()
                    bridging_count = np.full(b.shape[0], 0)
                else:
                    assignable_dtype = resolve_dtype(bridging_values.dtype, b.dtype)
                    assigned = b.astype(assignable_dtype)

                if ndim == 1:
                    # a single array has either NaN or non-NaN values; will only fill in NaN if we have a caried value from the previous block
                    if bridging_values is not None: # sel has at least one NaN
                        bridging_isnotna = ~bridging_isna # type: ignore #pylint: disable=E1130

                        sel_sided = sel & bridging_isnotna
                        if limit:
                            # set to false those values where bridging already at limit
                            sel_sided[bridging_count >= limit] = False # type: ignore

                        # set values in assigned if there is a NaN here (sel_sided) and we are not beyond the count
                        assigned[sel_sided] = bridging_values[sel_sided]
                        # only increment positions that are NaN here and have not-nan bridging values
                        sel_count_increment = sel & bridging_isnotna
                        bridging_count[sel_count_increment] += 1 # type: ignore
                        # set unassigned to zero
                        bridging_count[~sel_count_increment] = 0 # type: ignore
                    else:
                        bridging_count = np.full(b.shape[0], 0)

                    bridging_values = assigned
                    bridging_isna = isna_array(bridging_values) # must reevaluate if assigned

                elif ndim == 2:

                    slots = b.shape[0] # axis 0 has column width
                    length = b.shape[1]

                    # set to True when can reset count to zero; this is always the case if the bridge src value is not NaN (before we do any filling)
                    bridging_count_reset = ~sel[:, bridge_src_index]

                    if bridging_values is not None:
                        bridging_isnotna = ~bridging_isna #type: ignore #pylint: disable=E1130

                        # find leading NaNs segments if they exist, and if there is as corrresponding non-nan value to bridge
                        isna_entry = sel[:, bridge_dst_index] & bridging_isnotna
                        # get a row of Booleans for plausible candidates
                        candidates = (i for i, j in enumerate(isna_entry) if j == True)
                        sels_nonzero = ((i, sel[i]) for i in candidates)

                        # get appropriate leading slice to cover nan region
                        for idx, sel_nonzero in sels_nonzero:
                            # indices of not-nan values, per row
                            targets = np.nonzero(~sel_nonzero)[0]
                            if len(targets):
                                if directional_forward:
                                    sel_slice = slice(0, targets[0])
                                else: # backward
                                    sel_slice = slice(targets[-1]+1, length)
                            else: # all are NaN
                                sel_slice = slice(0, length)

                            # truncate sel_slice by limit-
                            sided_len = len(range(*sel_slice.indices(length)))

                            if limit and bridging_count[idx] >= limit: # type: ignore #pylint: disable=R1724
                                # if already at limit, do not assign
                                bridging_count[idx] += sided_len # type: ignore
                                continue
                            elif limit and (bridging_count[idx] + sided_len) >= limit: # type: ignore
                                # trim slice to fit
                                shift = bridging_count[idx] + sided_len - limit # type: ignore
                                # shift should only be positive only here
                                if directional_forward:
                                    sel_slice = slice(
                                            sel_slice.start,
                                            sel_slice.stop - shift)
                                else:
                                    sel_slice = slice(
                                            sel_slice.start + shift,
                                            sel_slice.stop)

                            # update with full length or limited length?
                            bridging_count[idx] += sided_len # type: ignore
                            assigned[idx, sel_slice] = bridging_values[idx]

                    # handle each row (going horizontally) in isolation
                    target_indexes = binary_transition(sel, axis=1)
                    for i in range(slots):

                        target_index = target_indexes[i]
                        if target_index is None:
                            # found no transitions, so either all NaN or all not NaN; if all NaN, might have been filled in bridging; if had values, will aready identify as bridging_count_reset[i] == True
                            continue

                        target_values = b[i, target_index]

                        def slice_condition(target_slice: slice) -> bool:
                            return sel[i, target_slice][0] # type: ignore

                        target_slice = None
                        for target_slice, value in slices_from_targets(
                                target_index=target_index,
                                target_values=target_values,
                                length=length,
                                directional_forward=directional_forward,
                                limit=limit,
                                slice_condition=slice_condition
                                ):
                            assigned[i, target_slice] = value

                        # update counts from the last slice; this will have already been limited if necessary, but need to reflext contiguous values going into the next block; if slices does not go to edge; will identify as needing as reset
                        if target_slice is not None:
                            bridging_count[i] = len(range(*target_slice.indices(length))) # type: ignore

                    bridging_values = assigned[:, bridge_src_index]
                    bridging_isna = isna_array(bridging_values) # must reevaluate if assigned

                    # if the birdging values is NaN now, it could not be filled, or was not filled enough, and thus does not continue a count; can set to zero
                    bridging_count_reset |= bridging_isna
                    bridging_count[bridging_count_reset] = 0 # type: ignore

                assigned.flags.writeable = False
                yield assigned


    def fillna_forward(self,
            limit: int = 0,
            *,
            axis: int = 0) -> 'TypeBlocks':
        '''Return a new ``TypeBlocks`` after feeding forward the last non-null (NaN or None) observation across contiguous nulls. Forward axis 0 fills columns, going from top to bottom. Forward axis 1 fills rows, going from left to right.
        '''
        if axis == 0:
            return self.from_blocks(self._fillna_directional_axis_0(
                    blocks=self._blocks,
                    directional_forward=True,
                    limit=limit
                    ))
        elif axis == 1:
            return self.from_blocks(self._fillna_directional_axis_1(
                    blocks=self._blocks,
                    directional_forward=True,
                    limit=limit
                    ))

        raise NotImplementedError(f'no support for axis {axis}')


    def fillna_backward(self,
            limit: int = 0,
            *,
            axis: int = 0) -> 'TypeBlocks':
        '''Return a new ``TypeBlocks`` after feeding backward the last non-null (NaN or None) observation across contiguous nulls. Backward, axis 0 fills columns, going from bottom to top. Backward axis 1 fills rows, going from right to left.
        '''
        if axis == 0:
            return self.from_blocks(self._fillna_directional_axis_0(
                    blocks=self._blocks,
                    directional_forward=False,
                    limit=limit
                    ))
        elif axis == 1:
            blocks = reversed(tuple(self._fillna_directional_axis_1(
                    blocks=self._blocks,
                    directional_forward=False,
                    limit=limit
                    )))
            return self.from_blocks(blocks)

        raise NotImplementedError(f'no support for axis {axis}')



    #---------------------------------------------------------------------------

    def dropna_to_keep_locations(self,
            axis: int = 0,
            condition: tp.Callable[..., bool] = np.all,
    ) -> tp.Tuple[tp.Optional[np.ndarray], tp.Optional[np.ndarray]]:
        '''
        Return the row and column slices to extract the new TypeBlock. This is to be used by Frame, where the slices will be needed on the indices as well.

        Args:
            axis: Dimension to drop, where 0 will drop rows and 1 will drop columns based on the condition function applied to a Boolean array.
        '''
        # get a unified boolean array; as iisna will always return a Boolean, we can simply take the firtst block out of consolidation
        unified = next(self.consolidate_blocks(isna_array(b) for b in self._blocks))

        # flip axis to condition funcion
        condition_axis = 0 if axis else 1
        to_drop = condition(unified, axis=condition_axis)
        to_keep = np.logical_not(to_drop)

        if axis == 1:
            row_key = None
            column_key = to_keep
        else:
            row_key = to_keep
            column_key = None

        return row_key, column_key


    def fillna(self, value: object) -> 'TypeBlocks':
        '''
        Return a new TypeBlocks instance that fills missing values with the passed value.
        '''
        return self.from_blocks(
                self._assign_blocks_from_boolean_blocks(
                        targets=(isna_array(b) for b in self._blocks),
                        value=value)
                )


    #---------------------------------------------------------------------------
    # mutate

    def append(self, block: np.ndarray) -> None:
        '''Add a block; an array copy will not be made unless the passed in block is not immutable'''
        # NOTE: shape can be 0, 0 if empty, or any one dimension can be 0. if columns is 0 and rows is non-zero, that row count is binding for appending (though the array need no tbe appended); if columns is > 0 and rows is zero, that row is binding for appending (and the array should be appended).

        row_count = self._shape[0]

        # update shape
        if block.shape[0] != row_count:
            raise RuntimeError(f'appended block shape {block.shape} does not align with shape {self._shape}')

        if block.ndim == 1:
            # length already confirmed to match row count; even if this is a zero length 1D array, we keep it as it (by definition) defines a column (if the existing row_count is zero). said another way, a zero length, 1D array always has a shape of (0, 1)
            block_columns = 1
        else:
            block_columns = block.shape[1]
            if block_columns == 0:
                # do not append 0 width arrays
                return

        # extend shape, or define it if not yet set
        self._shape = (row_count, self._shape[1] + block_columns)

        # add block, dtypes, index
        block_idx = len(self._blocks) # next block
        for i in range(block_columns):
            self._index.append((block_idx, i))
            self._dtypes.append(block.dtype)

        # make immutable copy if necessary before appending
        self._blocks.append(immutable_filter(block))

        # if already aligned, nothing to do
        if not self._row_dtype: # if never set as shape is empty
            self._row_dtype = block.dtype
        elif block.dtype != self._row_dtype:
            # we do not use resolve_dtype here as we want to preserve types, not safely cooerce them (i.e., int to float)
            self._row_dtype = DTYPE_OBJECT

    def extend(self,
            other: tp.Union['TypeBlocks', tp.Iterable[np.ndarray]]
            ) -> None:
        '''Extend this TypeBlock with the contents of another TypeBlocks instance, or an iterable of arrays. Note that an iterable of TypeBlocks is not currently supported.
        '''
        if isinstance(other, TypeBlocks):
            if self._shape[0]:
                if self._shape[0] != other._shape[0]:
                    raise RuntimeError('cannot extend unaligned shapes')
            blocks: tp.Iterable[np.ndarray] = other._blocks
        else: # accept iterables of np.arrays
            blocks = other
        # row count must be the same
        for block in blocks:
            self.append(block)
