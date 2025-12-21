import torch
import torch.nn as nn
import torch.nn.functional as F

# Assuming these are imported from your local module as per the original code
from . import (
    conv1d,
    linear,
    relu,
    tanh,
    flatten,
    kaiming_normal_init,
    zeros_init,
)

# ============================================================
# PyTorch Models
# ============================================================

class TinyLidarNet(nn.Module):
    """Standard CNN model for LiDAR data (Conv5 + FC4).

    This model processes 1D LiDAR scan data through 5 convolutional layers
    followed by 4 fully connected layers.

    Attributes:
        conv1 (nn.Conv1d): First convolutional layer.
        conv2 (nn.Conv1d): Second convolutional layer.
        conv3 (nn.Conv1d): Third convolutional layer.
        conv4 (nn.Conv1d): Fourth convolutional layer.
        conv5 (nn.Conv1d): Fifth convolutional layer.
        fc1 (nn.Linear): First fully connected layer.
        fc2 (nn.Linear): Second fully connected layer.
        fc3 (nn.Linear): Third fully connected layer.
        fc4 (nn.Linear): Output fully connected layer.
    """

    def __init__(self, input_dim=1080, output_dim=2):
        """Initializes TinyLidarNet.

        Args:
            input_dim (int): The size of the input LiDAR scan array. Defaults to 1080.
            output_dim (int): The size of the output prediction. Defaults to 2.
        """
        super().__init__()

        # --- Convolutional Layers ---
        self.conv1 = nn.Conv1d(1, 24, kernel_size=10, stride=4)
        self.conv2 = nn.Conv1d(24, 36, kernel_size=8, stride=4)
        self.conv3 = nn.Conv1d(36, 48, kernel_size=4, stride=2)
        self.conv4 = nn.Conv1d(48, 64, kernel_size=3)
        self.conv5 = nn.Conv1d(64, 64, kernel_size=3)

        # --- Calculate Flatten Dimension ---
        with torch.no_grad():
            dummy_input = torch.zeros(1, 1, input_dim)
            x = self.conv5(self.conv4(self.conv3(self.conv2(self.conv1(dummy_input)))))
            flatten_dim = x.view(1, -1).shape[1]

        # --- Fully Connected Layers ---
        self.fc1 = nn.Linear(flatten_dim, 100)
        self.fc2 = nn.Linear(100, 50)
        self.fc3 = nn.Linear(50, 10)
        self.fc4 = nn.Linear(10, output_dim)

        self._initialize_weights()

    def _initialize_weights(self):
        """Initializes weights using Kaiming Normal (He) initialization."""
        for m in self.modules():
            if isinstance(m, (nn.Conv1d, nn.Linear)):
                nn.init.kaiming_normal_(m.weight, mode='fan_out', nonlinearity='relu')
                if m.bias is not None:
                    nn.init.constant_(m.bias, 0)

    def forward(self, x):
        """Defines the computation performed at every call.

        Args:
            x (torch.Tensor): Input tensor of shape (batch_size, 1, input_dim).

        Returns:
            torch.Tensor: Output tensor of shape (batch_size, output_dim) with Tanh activation.
        """
        x = F.relu(self.conv1(x))
        x = F.relu(self.conv2(x))
        x = F.relu(self.conv3(x))
        x = F.relu(self.conv4(x))
        x = F.relu(self.conv5(x))
        x = x.view(x.size(0), -1)
        x = F.relu(self.fc1(x))
        x = F.relu(self.fc2(x))
        x = F.relu(self.fc3(x))
        return torch.tanh(self.fc4(x))


class TinyLidarNetSmall(nn.Module):
    """Lightweight CNN model for LiDAR data (Conv3 + FC3).

    This model is a smaller version of TinyLidarNet, processing data through
    3 convolutional layers and 3 fully connected layers.
    """

    def __init__(self, input_dim=1080, output_dim=2):
        """Initializes TinyLidarNetSmall.

        Args:
            input_dim (int): The size of the input LiDAR scan array. Defaults to 1080.
            output_dim (int): The size of the output prediction. Defaults to 2.
        """
        super().__init__()

        self.conv1 = nn.Conv1d(1, 24, kernel_size=10, stride=4)
        self.conv2 = nn.Conv1d(24, 36, kernel_size=8, stride=4)
        self.conv3 = nn.Conv1d(36, 48, kernel_size=4, stride=2)

        with torch.no_grad():
            dummy_input = torch.zeros(1, 1, input_dim)
            x = self.conv3(self.conv2(self.conv1(dummy_input)))
            flatten_dim = x.view(1, -1).shape[1]

        self.fc1 = nn.Linear(flatten_dim, 100)
        self.fc2 = nn.Linear(100, 50)
        self.fc3 = nn.Linear(50, output_dim)

        self._initialize_weights()

    def _initialize_weights(self):
        """Initializes weights using Kaiming Normal (He) initialization."""
        for m in self.modules():
            if isinstance(m, (nn.Conv1d, nn.Linear)):
                nn.init.kaiming_normal_(m.weight, mode='fan_out', nonlinearity='relu')
                if m.bias is not None:
                    nn.init.constant_(m.bias, 0)

    def forward(self, x):
        """Defines the computation performed at every call.

        Args:
            x (torch.Tensor): Input tensor of shape (batch_size, 1, input_dim).

        Returns:
            torch.Tensor: Output tensor of shape (batch_size, output_dim) with Tanh activation.
        """
        x = F.relu(self.conv1(x))
        x = F.relu(self.conv2(x))
        x = F.relu(self.conv3(x))
        x = x.view(x.size(0), -1)
        x = F.relu(self.fc1(x))
        x = F.relu(self.fc2(x))
        return torch.tanh(self.fc3(x))


# ============================================================
# NumPy Inference Models (Exact Naming Match with PyTorch)
# ============================================================

class TinyLidarNetNp:
    """NumPy implementation of TinyLidarNet (Conv5 + FC4).

    This class provides a pure NumPy inference implementation that matches the
    architecture of the PyTorch `TinyLidarNet` class.

    Attributes:
        params (dict): Stores weights and biases for all layers.
        strides (dict): Stores stride values for convolutional layers.
        shapes (dict): Stores parameter shapes for initialization.
    """

    def __init__(self, input_dim=1080, output_dim=2):
        """Initializes TinyLidarNetNp.

        Args:
            input_dim (int): The size of the input LiDAR scan array. Defaults to 1080.
            output_dim (int): The size of the output prediction. Defaults to 2.
        """
        self.input_dim = input_dim
        self.output_dim = output_dim
        self.params = {}

        # Stride definitions
        self.strides = {'conv1': 4, 'conv2': 4, 'conv3': 2, 'conv4': 1, 'conv5': 1}

        # Shape definitions matching PyTorch
        self.shapes = {
            'conv1_weight': (24, 1, 10),  'conv1_bias': (24,),
            'conv2_weight': (36, 24, 8),  'conv2_bias': (36,),
            'conv3_weight': (48, 36, 4),  'conv3_bias': (48,),
            'conv4_weight': (64, 48, 3),  'conv4_bias': (64,),
            'conv5_weight': (64, 64, 3),  'conv5_bias': (64,),
        }

        flatten_dim = self._get_conv_output_dim()
        self.shapes.update({
            'fc1_weight': (100, flatten_dim), 'fc1_bias': (100,),
            'fc2_weight': (50, 100),          'fc2_bias': (50,),
            'fc3_weight': (10, 50),           'fc3_bias': (10,),
            'fc4_weight': (output_dim, 10),   'fc4_bias': (output_dim,),
        })

        self._initialize_weights()

    def _get_conv_output_dim(self):
        """Calculates the flattened dimension after the last convolution layer."""
        l = self.input_dim
        for i in range(1, 6):
            k = self.shapes[f'conv{i}_weight'][2]
            s = self.strides[f'conv{i}']
            l = (l - k) // s + 1
        c = self.shapes['conv5_weight'][0]
        return c * l

    def _initialize_weights(self):
        """Initializes weights using Kaiming Normal (fan_out) and biases to zero."""
        for name, shape in self.shapes.items():
            if name.endswith('_weight'):
                fan_out = shape[0] * (shape[2] if 'conv' in name else 1)
                self.params[name] = kaiming_normal_init(shape, fan_out)
            elif name.endswith('_bias'):
                self.params[name] = zeros_init(shape)

    def __call__(self, x):
        """Performs the forward pass of the model.

        Args:
            x (np.ndarray): Input array of shape (batch_size, 1, input_dim).

        Returns:
            np.ndarray: Output array of shape (batch_size, output_dim).
        """
        x = relu(conv1d(x, self.params['conv1_weight'], self.params['conv1_bias'], self.strides['conv1']))
        x = relu(conv1d(x, self.params['conv2_weight'], self.params['conv2_bias'], self.strides['conv2']))
        x = relu(conv1d(x, self.params['conv3_weight'], self.params['conv3_bias'], self.strides['conv3']))
        x = relu(conv1d(x, self.params['conv4_weight'], self.params['conv4_bias'], self.strides['conv4']))
        x = relu(conv1d(x, self.params['conv5_weight'], self.params['conv5_bias'], self.strides['conv5']))
        x = flatten(x)
        x = relu(linear(x, self.params['fc1_weight'], self.params['fc1_bias']))
        x = relu(linear(x, self.params['fc2_weight'], self.params['fc2_bias']))
        x = relu(linear(x, self.params['fc3_weight'], self.params['fc3_bias']))
        return tanh(linear(x, self.params['fc4_weight'], self.params['fc4_bias']))


class TinyLidarNetSmallNp:
    """NumPy implementation of TinyLidarNetSmall (Conv3 + FC3).

    This class provides a pure NumPy inference implementation that matches the
    architecture of the PyTorch `TinyLidarNetSmall` class.
    """

    def __init__(self, input_dim=1080, output_dim=2):
        """Initializes TinyLidarNetSmallNp.

        Args:
            input_dim (int): The size of the input LiDAR scan array. Defaults to 1080.
            output_dim (int): The size of the output prediction. Defaults to 2.
        """
        self.input_dim = input_dim
        self.output_dim = output_dim
        self.params = {}
        self.strides = {'conv1': 4, 'conv2': 4, 'conv3': 2}

        self.shapes = {
            'conv1_weight': (24, 1, 10),  'conv1_bias': (24,),
            'conv2_weight': (36, 24, 8),  'conv2_bias': (36,),
            'conv3_weight': (48, 36, 4),  'conv3_bias': (48,),
        }

        flatten_dim = self._get_conv_output_dim()
        self.shapes.update({
            'fc1_weight': (100, flatten_dim), 'fc1_bias': (100,),
            'fc2_weight': (50, 100),          'fc2_bias': (50,),
            'fc3_weight': (output_dim, 50),   'fc3_bias': (output_dim,),
        })

        self._initialize_weights()

    def _get_conv_output_dim(self):
        """Calculates the flattened dimension after the last convolution layer."""
        l = self.input_dim
        for i in range(1, 4):
            k = self.shapes[f'conv{i}_weight'][2]
            s = self.strides[f'conv{i}']
            l = (l - k) // s + 1
        c = self.shapes['conv3_weight'][0]
        return c * l

    def _initialize_weights(self):
        """Initializes weights using Kaiming Normal (fan_out) and biases to zero."""
        for name, shape in self.shapes.items():
            if name.endswith('_weight'):
                fan_out = shape[0] * (shape[2] if 'conv' in name else 1)
                self.params[name] = kaiming_normal_init(shape, fan_out)
            elif name.endswith('_bias'):
                self.params[name] = zeros_init(shape)

    def __call__(self, x):
        """Performs the forward pass of the model.

        Args:
            x (np.ndarray): Input array of shape (batch_size, 1, input_dim).

        Returns:
            np.ndarray: Output array of shape (batch_size, output_dim).
        """
        x = relu(conv1d(x, self.params['conv1_weight'], self.params['conv1_bias'], self.strides['conv1']))
        x = relu(conv1d(x, self.params['conv2_weight'], self.params['conv2_bias'], self.strides['conv2']))
        x = relu(conv1d(x, self.params['conv3_weight'], self.params['conv3_bias'], self.strides['conv3']))
        x = flatten(x)
        x = relu(linear(x, self.params['fc1_weight'], self.params['fc1_bias']))
        x = relu(linear(x, self.params['fc2_weight'], self.params['fc2_bias']))
        return tanh(linear(x, self.params['fc3_weight'], self.params['fc3_bias']))
