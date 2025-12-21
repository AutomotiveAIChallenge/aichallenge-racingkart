import numpy as np

def kaiming_normal_init(shape, fan_out):
    """Initializes an array using Kaiming (He) Normal initialization.

    This function implements the initialization scheme described in "Delving Deep into
    Rectifiers: Surpassing Human-Level Performance on ImageNet Classification" (He et al.),
    specifically for the 'fan_out' mode. The values are drawn from a normal distribution
    centered at 0 with a standard deviation of sqrt(2.0 / fan_out).

    Args:
        shape (tuple): The shape of the output array (e.g., (3, 3) or (64,)).
        fan_out (int): The number of output units in the weight tensor.

    Returns:
        np.ndarray: An array of the specified shape initialized with random values.
    """
    std = np.sqrt(2.0 / fan_out)
    return np.random.normal(0, std, size=shape)

def zeros_init(shape):
    """Initializes an array with zeros.

    Args:
        shape (tuple): The shape of the output array.

    Returns:
        np.ndarray: An array of the specified shape filled with zeros.
    """
    return np.zeros(shape)
