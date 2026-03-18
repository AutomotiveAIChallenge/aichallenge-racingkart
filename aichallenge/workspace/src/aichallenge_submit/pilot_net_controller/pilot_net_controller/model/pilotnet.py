from . import (
    conv2d,
    linear,
    relu,
    tanh,
    flatten,
    kaiming_normal_init,
    zeros_init,
)


class PilotNetNp:
    """NumPy implementation of PilotNet (Conv5 + FC4).

    Provides pure NumPy inference matching the PyTorch PilotNet architecture.

    Attributes:
        params (dict): Stores weights and biases for all layers.
        strides (dict): Stores stride values for convolutional layers.
        shapes (dict): Stores parameter shapes for initialization.
    """

    def __init__(self, image_height=256, image_width=384, output_dim=2):
        self.image_height = image_height
        self.image_width = image_width
        self.output_dim = output_dim
        self.params = {}

        # Stride definitions (as tuples for conv2d)
        self.strides = {
            'conv1': (2, 2), 'conv2': (2, 2), 'conv3': (2, 2),
            'conv4': (1, 1), 'conv5': (1, 1)
        }

        # Shape definitions matching PyTorch (out_ch, in_ch, kh, kw)
        self.shapes = {
            'conv1_weight': (24, 3, 5, 5),   'conv1_bias': (24,),
            'conv2_weight': (36, 24, 5, 5),   'conv2_bias': (36,),
            'conv3_weight': (48, 36, 5, 5),   'conv3_bias': (48,),
            'conv4_weight': (64, 48, 3, 3),   'conv4_bias': (64,),
            'conv5_weight': (64, 64, 3, 3),   'conv5_bias': (64,),
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
        h, w = self.image_height, self.image_width
        for i in range(1, 6):
            kh, kw = self.shapes[f'conv{i}_weight'][2], self.shapes[f'conv{i}_weight'][3]
            sh, sw = self.strides[f'conv{i}']
            h = (h - kh) // sh + 1
            w = (w - kw) // sw + 1
        c = self.shapes['conv5_weight'][0]
        return c * h * w

    def _initialize_weights(self):
        for name, shape in self.shapes.items():
            if name.endswith('_weight'):
                if 'conv' in name:
                    fan_out = shape[0] * shape[2] * shape[3]
                else:
                    fan_out = shape[0]
                self.params[name] = kaiming_normal_init(shape, fan_out)
            elif name.endswith('_bias'):
                self.params[name] = zeros_init(shape)

    def __call__(self, x):
        """Forward pass.
        Args:
            x (np.ndarray): Input array of shape (batch_size, 3, image_height, image_width).
        Returns:
            np.ndarray: Output array of shape (batch_size, output_dim).
        """
        x = relu(conv2d(x, self.params['conv1_weight'], self.params['conv1_bias'], self.strides['conv1']))
        x = relu(conv2d(x, self.params['conv2_weight'], self.params['conv2_bias'], self.strides['conv2']))
        x = relu(conv2d(x, self.params['conv3_weight'], self.params['conv3_bias'], self.strides['conv3']))
        x = relu(conv2d(x, self.params['conv4_weight'], self.params['conv4_bias'], self.strides['conv4']))
        x = relu(conv2d(x, self.params['conv5_weight'], self.params['conv5_bias'], self.strides['conv5']))
        x = flatten(x)
        x = relu(linear(x, self.params['fc1_weight'], self.params['fc1_bias']))
        x = relu(linear(x, self.params['fc2_weight'], self.params['fc2_bias']))
        x = relu(linear(x, self.params['fc3_weight'], self.params['fc3_bias']))
        return tanh(linear(x, self.params['fc4_weight'], self.params['fc4_bias']))
