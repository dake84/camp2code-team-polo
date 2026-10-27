
from pathlib import Path

import pandas as pd
import numpy as np
import warnings

import seaborn as sns

warnings.filterwarnings("ignore")
import matplotlib.pyplot as plt
import tensorflow as tf

def import_data(data_path = Path('Data_AI/2026-10-26_13-38-11'),
                    image_height = 120,
                    image_width = 160):

    # Lade CSV
    csv_path = data_path / 'data.csv'
    data = pd.read_csv(csv_path)

    # Bilder Laden
    images = []

    for image_name in data['image']:

        image_path = data_path / image_name

        image = tf.keras.utils.load_img(
            image_path,
            target_size=(image_height, image_width)
        )

        image = tf.keras.utils.img_to_array(image)
        images.append(image)

    # Input Daten (Bilder)
    X = np.array(images, dtype=np.float32)

    # Output Daten (Parameter)
    Y = data[['speed', 'steering_angle']].to_numpy(dtype=np.float32)

    return X , Y

# Daten einlesen
data_path = Path('Data_AI/2026-10-26_13-38-11')

#X, Y = import_data(data_path = data_path)

data = pd.read_csv( data_path / 'data.csv')
sns.pairplot(data=data)