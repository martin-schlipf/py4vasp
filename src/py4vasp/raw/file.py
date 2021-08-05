from contextlib import AbstractContextManager
from pathlib import Path
from .rawdata import *
import h5py
import py4vasp.exceptions as exception


class File(AbstractContextManager):
    """Extract raw data from the HDF5 file.

    This class opens a given HDF5 file and its functions then provide access to
    the raw data via dataclasses. When you request the dataclass for a certain
    quantity, this class will generate the necessary pointers to the relevant
    HDF5 datasets, which can then be accessed like numpy arrays.

    This class also extends a context manager so it can be used to automatically
    deal with closing the HDF5 file. You cannot access the data in the
    dataclasses after you closed the HDF5 file.

    Parameters
    ----------
    filename : str or Path
        Name of the file from which the data is read (defaults to default_filename).

    Notes
    -----
    Except for scalars this class does not actually load the data from file. It
    only creates a pointer to the correct position in the HDF5 file. So you need
    to extract the data before closing the file. This lazy loading significantly
    enhances the performance if you are only interested in a subset of the data.
    """

    default_filename = "vaspout.h5"
    "Name of the HDF5 file Vasp creates."

    def __init__(self, filename=None):
        filename = self._actual_filename(filename)
        self.closed = False
        try:
            self._h5f = h5py.File(filename, "r")
        except OSError as err:
            error_message = (
                f"Error opening {filename} to read the data. Please check that you "
                "already completed the Vasp calculation and that the file is indeed "
                "in the directory. Please also check whether you are running the "
                "Python script in the same directory or pass the appropriate filename "
                "including the path."
            )
            raise exception.FileAccessError(error_message) from err

    def _actual_filename(self, filename):
        if filename is None:
            return File.default_filename
        elif Path(filename).is_dir():
            return Path(filename) / File.default_filename
        else:
            return filename

    @property
    def version(self):
        """Read the version number of Vasp.

        Returns
        -------
        RawVersion
            The major, minor, and patch number of Vasp.
        """
        self._raise_error_if_closed()
        return RawVersion(
            major=self._h5f["version/major"][()],
            minor=self._h5f["version/minor"][()],
            patch=self._h5f["version/patch"][()],
        )

    @property
    def dos(self):
        """Read all the electronic density of states (Dos) information.

        Returns
        -------
        DataDict[str, RawDos or None]
            The key of the dictionary specifies the kind of the Dos. The value
            contains  list of energies E and the associated raw electronic Dos
            D(E). The energies need to be manually shifted to the Fermi energy.
            If available, the projections on a set of projectors are included.
        """
        return self._make_data_dict(
            self._read_dos(), kpoints_opt=self._read_dos("_kpoints_opt")
        )

    def _read_dos(self, suffix=""):
        self._raise_error_if_closed()
        if f"results/electron_dos{suffix}/energies" not in self._h5f:
            return None
        return RawDos(
            fermi_energy=self._h5f[f"results/electron_dos{suffix}/efermi"][()],
            energies=self._h5f[f"results/electron_dos{suffix}/energies"],
            dos=self._h5f[f"results/electron_dos{suffix}/dos"],
            projectors=self._read_projectors(suffix),
            projections=self._safe_get_key(f"results/electron_dos{suffix}/dospar"),
        )

    @property
    def band(self):
        """Read all the band structures generated by Vasp.

        Returns
        -------
        DataDict[str, RawBand or None]
            The key of the dictionary specifies the kind of the band structure.
            The value contains the raw electronic eigenvalues at the specific
            **k** points. These values need to be manually aligned to the Fermi
            energy if desired. If available the projections on a set of
            projectors are included.
        """
        return self._make_data_dict(
            self._read_band(), kpoints_opt=self._read_band("_kpoints_opt")
        )

    def _read_band(self, suffix=""):
        self._raise_error_if_closed()
        if f"results/electron_eigenvalues{suffix}" not in self._h5f:
            return None
        return RawBand(
            fermi_energy=self._h5f[f"results/electron_dos{suffix}/efermi"][()],
            kpoints=self._read_kpoints(suffix),
            eigenvalues=self._h5f[f"results/electron_eigenvalues{suffix}/eigenvalues"],
            occupations=self._h5f[f"results/electron_eigenvalues{suffix}/fermiweights"],
            projectors=self._read_projectors(suffix),
            projections=self._safe_get_key(f"results/projectors{suffix}/par"),
        )

    @property
    def topology(self):
        """Read all the topology data used in the Vasp calculation.

        Returns
        -------
        DataDict[str, RawTopology]
            The key of the dictionary contains information about the kind of the
            topology. The value contains the information which ion types were
            used and how many ions of each type there are.
        """
        return self._make_data_dict(self._read_topology())

    def _read_topology(self):
        self._raise_error_if_closed()
        return RawTopology(
            ion_types=self._h5f["results/positions/ion_types"],
            number_ion_types=self._h5f["results/positions/number_ion_types"],
        )

    @property
    def trajectory(self):
        """Read all the trajectory data of an ionic relaxation or MD simulation.

        Returns
        -------
        DataDict[str, RawTrajectory]
            The key of the dictionary specifies which trajectory is contained.
            The value contains the topology of the crystal, the position of all
            atoms and the shape of the unit cell for all ionic steps.
        """
        return self._make_data_dict(self._read_trajectory())

    def _read_trajectory(self):
        self._raise_error_if_closed()
        return RawTrajectory(
            topology=self._read_topology(),
            positions=self._h5f["intermediate/ion_dynamics/position_ions"],
            lattice_vectors=self._h5f["intermediate/ion_dynamics/lattice_vectors"],
        )

    @property
    def projectors(self):
        """Read all the information about projectors if present.

        Returns
        -------
        DataDict[str, RawProjectors or None]
            If Vasp was set to produce the orbital decomposition of the bands
            the associated projector information is returned. The key specifies
            which kind of projectors are returned, the value lists the topology,
            the orbital types and the number of spins.
        """
        return self._make_data_dict(
            self._read_projectors(), kpoints_opt=self._read_projectors("_kpoints_opt")
        )

    def _read_projectors(self, suffix=""):
        self._raise_error_if_closed()
        if f"results/projectors{suffix}" not in self._h5f:
            return None
        eigenvalues_key = f"results/electron_eigenvalues{suffix}/eigenvalues"
        return RawProjectors(
            topology=self._read_topology(),
            orbital_types=self._h5f[f"results/projectors{suffix}/lchar"],
            number_spins=len(self._h5f[eigenvalues_key]),
        )

    @property
    def kpoints(self):
        """Read all the **k** points at which Vasp evaluated the orbitals
        and eigenvalues.

        Returns
        -------
        DataDict[str, RawKpoints or None]
            The key of the dictionary specifies the kind of the **k** point
            grid. For the value, the coordinates of the **k** points and the
            cell information is returned. Added is some information given in
            the input file about the generation and labels of the **k** points,
            which may be useful for band structures.
        """
        return self._make_data_dict(
            self._read_kpoints(), kpoints_opt=self._read_kpoints("_kpoints_opt")
        )

    def _read_kpoints(self, suffix=""):
        self._raise_error_if_closed()
        input = f"input/kpoints_opt" if suffix == "_kpoints_opt" else "input/kpoints"
        result = f"results/electron_eigenvalues{suffix}"
        if input not in self._h5f or result not in self._h5f:
            return None
        return RawKpoints(
            mode=self._h5f[f"{input}/mode"][()].decode(),
            number=self._h5f[f"{input}/number_kpoints"][()],
            coordinates=self._h5f[f"{result}/kpoint_coords"],
            weights=self._h5f[f"{result}/kpoints_symmetry_weight"],
            labels=self._safe_get_key(f"{input}/labels_kpoints"),
            label_indices=self._safe_get_key(f"{input}/positions_labels_kpoints"),
            cell=self._read_cell(),
        )

    @property
    def cell(self):
        """Read all the unit cell information of the crystal.

        Returns
        -------
        DataDict[str, RawCell]
            The key of the dictionary specified the kind of the unit cell.
            The value contains the lattice vectors of the unit cell and a
            scaling factor.
        """
        return self._make_data_dict(self._read_cell())

    def _read_cell(self):
        self._raise_error_if_closed()
        return RawCell(
            scale=self._h5f["results/positions/scale"][()],
            lattice_vectors=self._h5f["results/positions/lattice_vectors"],
        )

    @property
    def magnetism(self):
        """Read all the magnetization data of the crystal.

        Returns
        -------
        DataDict[str, RawMagnetism]
            The key specifies the kind of magnetization data and the value
            containes the magnetic moments and charges on every atom in orbital
            resolved representation.
        """
        return self._make_data_dict(self._read_magnetism())

    def _read_magnetism(self):
        self._raise_error_if_closed()
        key = "intermediate/ion_dynamics/magnetism/moments"
        if key not in self._h5f:
            return None
        return RawMagnetism(moments=self._h5f[key])

    @property
    def structure(self):
        """Read all the structural information.

        Returns
        -------
        DataDict[str, RawStructure]
            The key of the dictionary specifies the kind of the structure.
            The value contains the unit cell, the position of all the atoms
            and the magnetic moments.
        """
        return self._make_data_dict(self._read_structure())

    def _read_structure(self):
        self._raise_error_if_closed()
        return RawStructure(
            topology=self._read_topology(),
            cell=self._read_cell(),
            positions=self._h5f["results/positions/position_ions"],
            magnetism=self._read_magnetism(),
        )

    @property
    def energy(self):
        """Read all the energies during the ionic convergence.

        Returns
        -------
        DataDict[str, RawEnergy]
            The key of the dictionary specifies the kind of energy contained.
            The value contains a label for all energies and the values for
            every step in the relaxation or MD simulation.
        """
        return self._make_data_dict(self._read_energy())

    def _read_energy(self):
        self._raise_error_if_closed()
        return RawEnergy(
            labels=self._h5f["intermediate/ion_dynamics/energies_tags"],
            values=self._h5f["intermediate/ion_dynamics/energies"],
        )

    @property
    def density(self):
        """Read the charge and potentially magnetization density.

        Returns
        -------
        DataDict[str, RawDensity]
            The key informs about the kind of density reported. The value
            represents the density on the Fourier grid in the unit cell.
            Structural information is added for convenient plotting.
        """
        return self._make_data_dict(self._read_density())

    def _read_density(self):
        self._raise_error_if_closed()
        return RawDensity(
            structure=self._read_structure(),
            charge=self._h5f["charge/charge"],
        )

    def close(self):
        "Close the associated HDF5 file (automatically if used as context manager)."
        self._h5f.close()
        self.closed = True

    def __exit__(self, exc_type, exc_value, traceback):
        self.close()

    def _make_data_dict(self, default, **other):
        return DataDict({"default": default, **other}, self.version)

    def _raise_error_if_closed(self):
        if self.closed:
            raise exception.FileAccessError("I/O operation on closed file.")

    def _safe_get_key(self, key):
        if key in self._h5f:
            return self._h5f[key]
        else:
            return None
