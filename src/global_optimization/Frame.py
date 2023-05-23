from pathlib import Path
from typing import Dict, Optional, List, Generic, Tuple
import numpy.typing as npt
import numpy as np
import pandas as pd
from PIL import Image
import random

from copy import deepcopy
from collections import defaultdict

from .Cells import Cell
from .Config import SimulationConfig


class Frame:
    def __init__(self, real_image_stack: npt.NDArray, simulation_config: SimulationConfig, cells: List[Cell], output_path: Path, image_name: str):
        self.z_slices = [simulation_config.z_scaling * (i - simulation_config.z_slices // 2) for i in range(simulation_config.z_slices)]
        self.cells = cells
        self.simulation_config = simulation_config
        self.output_path = output_path
        self.image_name = image_name  # name of image file for saving cell data

        self._real_image_stack = real_image_stack  # original 3d array of images
        self.real_image_stack = np.array(self._real_image_stack)  # create a copy of the original image stack

        self.pad_real_image()
        # self.cell_map_stack = self.generate_cell_maps()
        self.synth_image_stack = self.generate_synth_images()

    # def update(self):
    #     """Update the frame."""
    #     self.pad_real_image()
    #     self.synth_image_stack = self.generate_synth_images()
    #     self.cell_map_stack = self.generate_cell_maps()
    #
    # def update_simulation_config(self, simulation_config: SimulationConfig):
    #     """Update the simulation config and regenerate the synthetic images and cell maps."""
    #     self.simulation_config = simulation_config
    #     self.update()

    def generate_synth_images(self):
        """Generate synthetic images from the cells in the frame."""
        if self.cells is None:
            raise ValueError("Cells are not set")

        shape = self.get_image_shape()
        synth_image_stack = []

        for i, z in enumerate(self.z_slices):
            synth_image = np.full(shape, self.simulation_config.background_color)
            for cell in self.cells:
                cell.draw(synth_image, self.simulation_config, z = z)
            synth_image_stack.append(synth_image)

        return np.array(synth_image_stack)

    def calculate_cost(self, synth_image_stack: npt.NDArray):
        """Calculate the L2 cost of the synthetic images."""
        return float(np.linalg.norm(self.real_image_stack - synth_image_stack))

    # def generate_cell_maps(self):
    #     """Generate cell maps from the cells in the frame. This should only be for binary images"""
    #     # TODO: Implement this
    #     return np.zeros(self.real_image_stack.shape)

    def pad_real_image(self):
        """Pad the real image to account for the padding in the synthetic images."""
        padding = ((0, 0), (self.simulation_config.padding, self.simulation_config.padding), (self.simulation_config.padding, self.simulation_config.padding))
        self.real_image_stack = np.pad(self.real_image_stack, padding, mode='constant', constant_values=self.simulation_config.background_color)

    def get_image_shape(self):
        """Get the shape of an individual image in the frame."""
        return self.real_image_stack.shape[1:]

    def generate_output_images(self):
        """Generate the output images for the frame."""
        real_images_with_outlines: List[Image.Image] = []
        for real_image, z in zip(self.real_image_stack, self.z_slices):
            output_frame = np.stack((real_image,) * 3, axis=-1)
            for cell in self.cells:
                cell.draw_outline(output_frame, (1, 0, 0), z)
            output_frame = Image.fromarray(np.uint8(255 * output_frame))
            real_images_with_outlines.append(output_frame)
        return real_images_with_outlines

    def generate_output_synth_images(self):
        """Generate the output synthetic images for the frame."""
        return [Image.fromarray(np.uint8(255 * synth_image), "L") for synth_image in self.synth_image_stack]

    def get_cells_as_params(self):
        """Convert the cells in the frame to a pandas dataframe."""
        cell_params = pd.DataFrame([dict(cell.get_cell_params()) for cell in self.cells])
        # set the file name for all cells to the same file name
        cell_params["file"] = self.image_name
        return cell_params

    def __len__(self):
        return len(self.cells)


    def perturb(self):
        # randomly pick an index for a cell
        index = random.randint(0, len(self.cells) - 1)

        # store old cell
        old_cell = self.cells[index]

        # replace the cell at that index with a new cell
        self.cells[index] = self.cells[index].get_perturbed_cell()

        # synthesize new synthetic image
        new_synth_image_stack = self.generate_synth_images()

        # get the cost of the new synthetic image
        new_cost = self.calculate_cost(new_synth_image_stack)

        # if the difference is greater than the threshold, revert to the old cell
        old_cost = self.calculate_cost(self.synth_image_stack)

        def callback(accept: bool):
            if accept:
                self.cells[index] = old_cell
            else:
                self.synth_image_stack = new_synth_image_stack

        return old_cost - new_cost, callback


    def gradient_descent(self):

        directions = defaultdict(dict)

        # hyper parameters to tune for gradient descent
        moving_delta = 1
        delta = 1e-3
        alpha = 0.1

        cell_list = self.cells
        orig_cost = self.calculate_cost(self.synth_image_stack)

        # calculate gradient for each cell
        for index, cell in enumerate(cell_list):
            # iterate through each cell and calculate the gradient
            old_cell = deepcopy(cell)
            # keep old cell params
            params = cell.get_cell_params().__dict__
            param_gradients = {}

            # get gradients for x, y, z, radius
            for param, val in params.items():
                if param == 'name': # or param == 'radius':
                    continue
                
                # setup parameters to test perterb of size delta
                perterb_param = defaultdict(float)
                perterb_param[param] = moving_delta
                # perterb cell
                self.cells[index] = self.cells[index].get_paramaterized_cell(perterb_param)

                # generate new image stack
                new_synth_image_stack = self.generate_synth_images()
                # get new cost
                new_cost = self.calculate_cost(new_synth_image_stack)
                # calculate gradient direction for param
                param_gradients[param] = (new_cost - orig_cost) / delta
                # reset cell
                self.cells[index] = old_cell

            # calculate direction to move towards for gradient descent
            for param, gradient in param_gradients.items():
                directions[index][param] = -1 * alpha * gradient
            
        
        for index, cell in enumerate(cell_list):
            self.cells[index] = self.cells[index].get_paramaterized_cell(directions[index])
        
        self.synth_image_stack = self.generate_synth_images()
        new_cost = self.calculate_cost(self.synth_image_stack)
