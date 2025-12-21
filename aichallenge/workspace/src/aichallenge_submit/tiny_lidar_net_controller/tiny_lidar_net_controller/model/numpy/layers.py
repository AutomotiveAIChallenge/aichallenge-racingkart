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

def conv1d(x, weight, bias, stride):
    """Applies a 1D convolution over an input signal composed of several input planes.

    Args:
        x (np.ndarray): Input array of shape (batch_size, in_channels, length).
        weight (np.ndarray): Filters of shape (out_channels, in_channels, kernel_size).
        bias (np.ndarray): Bias vector of shape (out_channels,).
        stride (int): The stride of the convolving kernel.

    Returns:
        np.ndarray: The output of the convolution of shape
            (batch_size, out_channels, out_length).
    """
    n_x, c_in, l_in = x.shape
    c_out, _, k = weight.shape
    l_out = (l_in - k) // stride + 1
    s0, s1, s2 = x.strides
    strided_x = as_strided(x, shape=(n_x, c_in, l_out, k), strides=(s0, s1, s2 * stride, s2))
    strided_x_reshaped = strided_x.transpose(0, 2, 1, 3).reshape(n_x * l_out, c_in * k)
    weight_reshaped = weight.reshape(c_out, -1)
    conv_val = strided_x_reshaped @ weight_reshaped.T
    conv_val_reshaped = conv_val.reshape(n_x, l_out, c_out).transpose(0, 2, 1)
    return conv_val_reshaped + bias.reshape(1, -1, 1)

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

def max_pool2d(x, kernel_size=(2, 2), stride=(2, 2)):
    """Applies a 2D max pooling over an input signal.

    Args:
        x (np.ndarray): Input array of shape (batch_size, in_channels, height, width).
        kernel_size (tuple): The size of the pooling window (height, width).
            Defaults to (2, 2).
        stride (tuple): The stride of the window (stride_height, stride_width).
            Defaults to (2, 2).

    Returns:
        np.ndarray: The pooled output array.
    """
    n_x, c, h_in, w_in = x.shape
    k_h, k_w = kernel_size
    s_h, s_w = stride
    
    h_out = (h_in - k_h) // s_h + 1
    w_out = (w_in - k_w) // s_w + 1
    
    s0, s1, s2, s3 = x.strides
    
    strided_x = as_strided(x,
                           shape=(n_x, c, h_out, w_out, k_h, k_w),
                           strides=(s0, s1, s2 * s_h, s3 * s_w, s2, s3))
    
    return strided_x.max(axis=(4, 5))

def flatten(x):
    """Flattens the input while maintaining the batch dimension.

    Args:
        x (np.ndarray): Input array of shape (batch_size, ...).

    Returns:
        np.ndarray: A flattened array of shape (batch_size, num_features).
    """
    return x.reshape(x.shape[0], -1)

def sigmoid(x):
    """Applies the Sigmoid function element-wise.

    Args:
        x (np.ndarray): Input array of any shape.

    Returns:
        np.ndarray: An array with values mapped to the range (0, 1).
    """
    return 1 / (1 + np.exp(-x))

def softmax(x):
    """Applies the Softmax function along the feature dimension (axis 1).

    This implementation includes numerical stability improvements by subtracting
    the maximum value from the input before exponentiation.

    Args:
        x (np.ndarray): Input array, typically of shape (batch_size, num_classes).

    Returns:
        np.ndarray: An array of the same shape as input, where values along axis 1
            sum to 1.
    """
    x_max = np.max(x, axis=1, keepdims=True)
    exps = np.exp(x - x_max)
    return exps / np.sum(exps, axis=1, keepdims=True)
