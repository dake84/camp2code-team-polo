from pathlib import Path
from datetime import datetime

import pandas as pd
import numpy as np
import warnings

import seaborn as sns

warnings.filterwarnings('ignore')
import matplotlib.pyplot as plt
import tensorflow as tf

import cv2

from tensorflow.keras import Sequential
from tensorflow.keras.layers import Input, Rescaling, Conv2D, Flatten, Dropout, Dense, Cropping2D
from tensorflow.keras.callbacks import EarlyStopping, ModelCheckpoint


def import_data(data_path=Path('Data_AI'), lane_filter=True):
    """
    Durchsucht den übergeordneten data_path nach allen Unterordnern,
    die eine 'data.csv' enthalten, und lädt die Trainingsdaten.
    Wendet Horizontal-Spiegelung, Helligkeits- und Kontrast-Augmentation an.
    """
    data_path = Path(data_path)
    X = []
    Y = []

    image_height = None
    image_width = None
    image_dimension = None

    # Alle Unterordner finden, die eine data.csv besitzen (sortiert nach Name/Datum)
    sub_folders = sorted([p.parent for p in data_path.glob('*/data.csv')])

    if not sub_folders:
        sub_folders = sorted([p.parent for p in data_path.rglob('data.csv')])

    if not sub_folders:
        raise FileNotFoundError(f'Keine Unterordner mit data.csv in {data_path.resolve()} gefunden.')

    print(f'Gefundene Datensätze ({len(sub_folders)} Ordner):')
    for sf in sub_folders:
        print(f' - {sf.name}')

    for folder_path in sub_folders:
        csv_path = folder_path / 'data.csv'
        print('Lese CSV-Pfad:', csv_path.resolve())

        data = pd.read_csv(csv_path)

        if lane_filter:
            try:
                data = data[data['lane_count'] == 2]
            except KeyError:
                print(f'{folder_path.name}: lane_count nicht vorhanden -> Daten werden nicht gefiltert.')

        if data.empty:
            print(f'{folder_path.name}: keine passenden Datenzeilen -> übersprungen.')
            continue

        images = []
        valid_indices = []

        for idx, image_name in enumerate(data['image']):
            image_path = folder_path / image_name

            if not image_path.is_file():
                print(f'Warnung: {image_name} in {folder_path.name} nicht gefunden -> übersprungen.')
                continue

            image = cv2.imread(str(image_path))
            if image is None:
                print(f'Warnung: {image_name} konnte nicht gelesen werden -> übersprungen.')
                continue

            if image_height is None:
                image_height, image_width, image_dimension = image.shape
                print(f'Modell-Eingangsmaße festgelegt: H={image_height}, B={image_width}, C={image_dimension}')

            if (image.shape[0], image.shape[1]) != (image_height, image_width):
                image = cv2.resize(image, (image_width, image_height))

            images.append(image)
            valid_indices.append(idx)

        if not images:
            print(f'{folder_path.name}: keine Bilder gefunden -> übersprungen.')
            continue

        data = data.iloc[valid_indices]

        # 1. Originaldaten
        X_arr = np.array(images, dtype=np.float32)
        Y_arr = data[['speed', 'steering_angle']].to_numpy(dtype=np.float32)

        # 2. Horizontal spiegeln (Winkel invertieren: 180 - Winkel)
        X_flipped = tf.image.flip_left_right(X_arr).numpy()
        Y_flipped = Y_arr.copy()
        Y_flipped[:, 1] = 180.0 - Y_flipped[:, 1]

        # 3. Helligkeit & Kontrast auf Originale anwenden
        X_aug = tf.image.random_brightness(X_arr, max_delta=20.0)
        X_aug = tf.image.random_contrast(X_aug, lower=0.8, upper=1.2)
        X_aug = tf.clip_by_value(X_aug, 0.0, 255.0).numpy()
        Y_aug = Y_arr.copy()

        # 4. Helligkeit & Kontrast auf gespiegelte Bilder anwenden
        X_flipped_aug = tf.image.random_brightness(X_flipped, max_delta=20.0)
        X_flipped_aug = tf.image.random_contrast(X_flipped_aug, lower=0.8, upper=1.2)
        X_flipped_aug = tf.clip_by_value(X_flipped_aug, 0.0, 255.0).numpy()
        Y_flipped_aug = Y_flipped.copy()

        # Alle 4 Varianten zusammenführen
        X.extend([X_arr, X_flipped, X_aug, X_flipped_aug])
        Y.extend([Y_arr, Y_flipped, Y_aug, Y_flipped_aug])

    if not X:
        raise ValueError(f'Keine verwendbaren Daten in {data_path.resolve()} gefunden.')

    X = np.concatenate(X, axis=0)
    Y = np.concatenate(Y, axis=0)

    return X, Y, image_height, image_width, image_dimension, [sf.name for sf in sub_folders]


def write_txt(txt_path, lines, mode='a'):
    '''Schreibt Zeilen in die Parameter-/Ergebnis-.txt.'''
    with open(txt_path, mode, encoding='utf-8') as f:
        f.write('\n'.join(lines) + '\n')


def train_deepcar(data_path=Path('Data_AI'),
                  lane_filter=True,
                  epochs=100,
                  learning_rate=0.001,
                  batch_size=32,
                  train_split=0.70,
                  validate_split=0.15,
                  patience=8,
                  min_delta=0.01):

    data_path = Path(data_path)

    # Daten einlesen (alle Unterordner mit data.csv werden geladen und augmentiert)
    X, Y, fig_heigt, fig_width, fig_dim, loaded_folders = import_data(
        data_path=data_path,
        lane_filter=lane_filter
    )

    # eigener Trainingsordner Data_AI/Training_Datum_Uhrzeit
    timestamp = datetime.now().strftime('%Y-%m-%d_%H-%M-%S')
    save_path = data_path / f'Training_{timestamp}'
    save_path.mkdir(parents=True, exist_ok=True)
    txt_path = save_path / 'parameter.txt'

    # Parameter in .txt schreiben
    write_txt(txt_path, [
        f'Training: {timestamp}',
        '',
        '[Parameter]',
        f'data_path: {data_path}',
        f'loaded_sub_folders: {loaded_folders}',
        f'lane_filter: {lane_filter}',
        f'epochs (max): {epochs}',
        f'learning_rate: {learning_rate}',
        f'batch_size: {batch_size}',
        f'train_split: {train_split}',
        f'validate_split: {validate_split}',
        f'test_split: {round(1 - train_split - validate_split, 2)}',
        f'early_stopping patience: {patience}',
        f'early_stopping min_delta: {min_delta}',
        'augmentation: flip_left_right (180 - angle), random_brightness (+/- 20), random_contrast (0.8 - 1.2)',
        'color_space: BGR (OpenCV native)',
        'cropping: top=60, bottom=40, left=0, right=0',
    ], mode='w')

    # DataFrame für Pandas
    data = pd.DataFrame(Y, columns=['speed', 'steering_angle'])
    g = sns.pairplot(data=data[['speed', 'steering_angle']])
    g.savefig(save_path / 'pairplot_speed_steering.png', dpi=150)
    plt.close('all')

    print('X shape:', X.shape)
    print('Y shape:', Y.shape)
    print('Erster Output:', Y[0])

    # Datensätze mischen
    indices = np.random.permutation(len(X))
    X = X[indices]
    Y = Y[indices]

    # Dataset für Modell aufbauen
    dataset = tf.data.Dataset.from_tensor_slices((X, Y))
    dataset = dataset.shuffle(buffer_size=len(X), seed=42, reshuffle_each_iteration=False)

    n = len(X)
    n_train = int(train_split * n)
    n_validate = int(validate_split * n)

    train_ds = dataset.take(n_train)
    remaining_ds = dataset.skip(n_train)
    validate_ds = remaining_ds.take(n_validate)
    test_ds = remaining_ds.skip(n_validate)

    write_txt(txt_path, [
        '',
        '[Daten]',
        f'Bildgröße (H, B, Kanäle): ({fig_heigt}, {fig_width}, {fig_dim})',
        f'X shape: {X.shape}',
        f'Y shape: {Y.shape}',
        f'n_train: {n_train}',
        f'n_validate: {n_validate}',
        f'n_test: {n - n_train - n_validate}',
    ])

    # Netz aufbauen
    model = Sequential(
        [
            Input(shape=(fig_heigt, fig_width, fig_dim)),
            Cropping2D(cropping=((60, 40), (0, 0))),
            Rescaling(1./255),
            Conv2D(24, 5, strides=2, activation='relu'),
            Conv2D(36, 5, strides=2, activation='relu'),
            Conv2D(48, 5, strides=2, activation='relu'),
            Conv2D(64, 3, activation='relu'),
            Conv2D(64, 3, activation='relu'),
            Flatten(),
            Dropout(0.3),
            Dense(100, activation='relu'),
            Dense(50, activation='relu'),
            Dense(10, activation='relu'),
            Dense(2)
        ],
        name='DeepCar_CNN'
    )

    model.compile(
        optimizer=tf.keras.optimizers.Adam(learning_rate=learning_rate),
        loss='mse',
        metrics=['mae']
    )

    model.summary()

    summary_lines = []
    model.summary(print_fn=lambda line, **kwargs: summary_lines.append(line))
    write_txt(txt_path, ['', '[Modell]', *summary_lines])

    train_ds    = train_ds.shuffle(1000).batch(batch_size).prefetch(tf.data.AUTOTUNE)
    validate_ds = validate_ds.batch(batch_size).prefetch(tf.data.AUTOTUNE)
    test_ds     = test_ds.batch(batch_size).prefetch(tf.data.AUTOTUNE)

    print(train_ds.element_spec)

    early_stopping = EarlyStopping(
        monitor='val_loss',
        min_delta=min_delta,
        patience=patience,
        restore_best_weights=True,
        verbose=1
    )

    callbacks = [
        early_stopping,
        ModelCheckpoint(
            filepath=str(save_path / 'best_deepcar.keras'),
            monitor='val_loss',
            save_best_only=True
        )
    ]

    history = model.fit(
        train_ds,
        validation_data=validate_ds,
        epochs=epochs,
        callbacks=callbacks
    )

    model.save(save_path / 'deepcar.keras')

    test_loss, test_mae = model.evaluate(test_ds)
    print(f'Test-MSE: {test_loss:.4f}  Test-MAE: {test_mae:.4f}')

    y_true = np.concatenate([y.numpy() for _, y in test_ds]); y_pred = model.predict(test_ds, verbose=0)
    y_mean = np.concatenate([y.numpy() for _, y in train_ds]).mean(axis=0)

    mae_per_output = np.abs(y_pred - y_true).mean(axis=0)
    mae_baseline = np.abs(y_mean - y_true).mean(axis=0)
    r2 = 1 - ((y_pred - y_true)**2).mean(axis=0) / y_true.var(axis=0)

    print('MAE  [speed, steering]:', mae_per_output)
    print('Basis-MAE (nur Mittelwert):', mae_baseline)
    print('R² [speed, steering]:', r2)

    # TFLite-Export
    tflite_path = save_path / 'deepcar.tflite'
    converter = tf.lite.TFLiteConverter.from_keras_model(model)
    converter.optimizations = [tf.lite.Optimize.DEFAULT]
    tflite_path.write_bytes(converter.convert())

    interp = tf.lite.Interpreter(model_path=str(tflite_path)); interp.allocate_tensors()
    inp, out = interp.get_input_details()[0], interp.get_output_details()[0]
    x, y = next(iter(test_ds.unbatch().batch(1)))
    interp.set_tensor(inp['index'], x.numpy().astype(np.float32)); interp.invoke()
    keras_out, tflite_out, soll = model.predict(x, verbose=0)[0], interp.get_tensor(out['index'])[0], y.numpy()[0]
    print('Keras :', keras_out); print('TFLite:', tflite_out); print('Soll  :', soll)

    h = history.history; ep = np.arange(1, len(h['loss']) + 1); best = int(np.argmin(h['val_loss'])) + 1
    epochs_trained = len(h['loss'])
    stopped_early = epochs_trained < epochs

    write_txt(txt_path, [
        '',
        '[Ergebnisse]',
        f'trainierte Epochen: {epochs_trained} von max. {epochs}',
        f'früh gestoppt (EarlyStopping): {stopped_early}',
        f'beste Epoche (val_loss): {best}',
        f'val_loss (beste Epoche): {h["val_loss"][best - 1]:.4f}',
        f'Test-MSE: {test_loss:.4f}',
        f'Test-RMSE: {np.sqrt(test_loss):.4f}',
        f'Test-MAE: {test_mae:.4f}',
        f'MAE [speed, steering]: {mae_per_output}',
        f'Basis-MAE (nur Mittelwert): {mae_baseline}',
        f'R² [speed, steering]: {r2}',
        f'Kontrolle Keras : {keras_out}',
        f'Kontrolle TFLite: {tflite_out}',
        f'Kontrolle Soll  : {soll}',
    ])

    fig, ax = plt.subplots(1, 2, figsize=(13, 4.5))
    ax[0].plot(ep, h['loss'], label='Training')
    ax[0].plot(ep, h['val_loss'], label='Validierung')
    ax[0].axvline(best, color='gray', ls='--', label=f'beste Epoche {best}')
    ax[0].set_yscale('log')
    ax[0].set(title='Loss (MSE)', xlabel='Epoche', ylabel='MSE')
    ax[0].legend(); ax[0].grid(alpha=0.3)
    ax[1].plot(ep, h['mae'], label='Training')
    ax[1].plot(ep, h['val_mae'], label='Validierung')
    ax[1].axvline(best, color='gray', ls='--')
    ax[1].set(title='MAE', xlabel='Epoche', ylabel='MAE')
    ax[1].legend()
    ax[1].grid(alpha=0.3)
    plt.tight_layout()
    fig.savefig(save_path / 'training_loss_mae.png', dpi=150)
    plt.close(fig)

    names = ['speed', 'steering_angle']
    fig, ax = plt.subplots(2, 2, figsize=(13, 9))
    for i, name in enumerate(names):
        lim = [y_true[:, i].min(), y_true[:, i].max()]
        ax[0, i].scatter(y_true[:, i], y_pred[:, i], s=8, alpha=0.5)
        ax[0, i].plot(lim, lim, 'r--', label='ideal')
        ax[0, i].set(title=f'{name}: Vorhersage vs. echt', xlabel='echt', ylabel='vorhergesagt')
        ax[0, i].legend(); ax[0, i].grid(alpha=0.3)
        ax[1, i].hist(y_pred[:, i] - y_true[:, i], bins=50)
        ax[1, i].set(title=f'{name}: Fehlerverteilung', xlabel='Fehler'); ax[1, i].grid(alpha=0.3)

    plt.tight_layout()
    fig.savefig(save_path / 'test_vorhersage_vs_echt.png', dpi=150)
    plt.close(fig)

    fig = plt.figure(figsize=(13, 4))
    plt.plot(y_true[:, 1], label='echt')
    plt.plot(y_pred[:, 1], label='Netz', alpha=0.8)
    plt.title('Lenkwinkel über die Testbilder')
    plt.xlabel('Bild-Index')
    plt.ylabel('steering_angle')
    plt.legend()
    plt.grid(alpha=0.3)
    fig.savefig(save_path / 'test_lenkwinkel_verlauf.png', dpi=150)
    plt.close(fig)

    print('Plots, parameter.txt, deepcar.keras und deepcar.tflite gespeichert in:', save_path)

    return model, history


if __name__ == '__main__':
    print('Starting DeepCar Training...')

    daten_ordner = Path(r'C:\Users\LtCHa\Desktop\Coding\DeepCar\Training_Data')

    model, history = train_deepcar(
        data_path=daten_ordner,
        lane_filter=False,
        epochs=100,
        patience=8,
        min_delta=0.01
    )