import numpy as np
from numpy.lib.stride_tricks import as_strided

def relu(x):
    """Applies the Rectified Linear Unit (ReLU) function element-wise.

    Args:
        x (np.ndarray): Input array of any shape.

    Returns:
        np.ndarray: An array where negative values are replaced by zero.
    """
    return np.maximum(0, x)

def tanh(x):
    """Applies the Hyperbolic Tangent (Tanh) function element-wise.

    Args:
        x (np.ndarray): Input array of any shape.

    Returns:
        np.ndarray: An array with values mapped to the range [-1, 1].
    """
    return np.tanh(x)

def linear(x, weight, bias):
    """Applies a linear transformation to the incoming data: y = xA^T + b.

    Args:
        x (np.ndarray): Input array of shape (batch_size, in_features).
        weight (np.ndarray): Weight matrix of shape (out_features, in_features).
        bias (np.ndarray): Bias vector of shape (out_features,).

    Returns:
        np.ndarray: The output of the linear transformation of shape
            (batch_size, out_features).
    """
    return np.dot(x, weight.T) + bias

def conv2d(x, weight, bias, stride=(1, 1)):
    """Applies a 2D convolution over an input image.

    Args:
        x (np.ndarray): Input array of shape (batch_size, in_channels, height, width).
        weight (np.ndarray): Filters of shape
            (out_channels, in_channels, kernel_height, kernel_width).
        bias (np.ndarray): Bias vector of shape (out_channels,).
        stride (tuple): A tuple of (stride_height, stride_width). Defaults to (1, 1).

    Returns:
        np.ndarray: The output of the convolution of shape
            (batch_size, out_channels, out_height, out_width).
    """
    n_x, c_in, h_in, w_in = x.shape
    c_out, _, k_h, k_w = weight.shape
    s_h, s_w = stride

    h_out = (h_in - k_h) // s_h + 1
    w_out = (w_in - k_w) // s_w + 1

    s0, s1, s2, s3 = x.strides

    strided_x = as_strided(x,
                           shape=(n_x, c_in, h_out, w_out, k_h, k_w),
                           strides=(s0, s1, s2 * s_h, s3 * s_w, s2, s3))

    strided_x_reshaped = strided_x.transpose(0, 2, 3, 1, 4, 5).reshape(n_x * h_out * w_out, c_in * k_h * k_w)

    weight_reshaped = weight.reshape(c_out, -1)

    conv_val = strided_x_reshaped @ weight_reshaped.T

    conv_val_reshaped = conv_val.reshape(n_x, h_out, w_out, c_out)

    conv_val_final = conv_val_reshaped.transpose(0, 3, 1, 2)

    return conv_val_final + bias.reshape(1, -1, 1, 1)

def flatten(x):
    """Flattens the input while maintaining the batch dimension.

    Args:
        x (np.ndarray): Input array of shape (batch_size, ...).

    Returns:
        np.ndarray: A flattened array of shape (batch_size, num_features).
    """
    return x.reshape(x.shape[0], -1)
