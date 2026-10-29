from pathlib import Path
from datetime import datetime
import pandas as pd
import numpy as np
import warnings
import seaborn as sns
import matplotlib.pyplot as plt
import tensorflow as tf
import cv2

warnings.filterwarnings('ignore')
from tensorflow.keras import Sequential
from tensorflow.keras.layers import Input, Rescaling, Conv2D, Flatten, Dropout, Dense, Cropping2D
from tensorflow.keras.callbacks import EarlyStopping, ModelCheckpoint


def import_data(data_path=Path('Data_AI')):
    """
    Lädt alle Datensätze extrem speicherschonend als uint8 (spart >85% RAM)
    und bereinigt automatisch Mülldaten (eingefrorene Lenkwinkel am Fahrtende).
    """
    data_path = Path(data_path)
    X = []
    Y = []

    image_height = None
    image_width = None
    image_dimension = None

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

        # 1. Doppelte Bild-Einträge entfernen
        data = data.drop_duplicates(subset=['image'], keep='first')

        # 2. Eingefrorene Lenkwinkel am Fahrtende / bei Spurverlust herausfiltern
        is_stuck = (data['steering_angle'].diff() == 0) & (data['steering_angle'] != 90.0)
        data = data[~is_stuck].reset_index(drop=True)

        if data.empty:
            print(f'{folder_path.name}: keine gültigen Daten nach Bereinigung -> übersprungen.')
            continue

        images = []
        valid_indices = []

        for idx, image_name in enumerate(data['image']):
            image_path = folder_path / image_name

            if not image_path.is_file():
                continue

            image = cv2.imread(str(image_path))
            if image is None:
                continue

            if image_height is None:
                image_height, image_width, image_dimension = image.shape
                print(f'Modell-Eingangsmaße festgelegt: H={image_height}, B={image_width}, C={image_dimension}')

            if (image.shape[0], image.shape[1]) != (image_height, image_width):
                image = cv2.resize(image, (image_width, image_height))

            images.append(image)
            valid_indices.append(idx)

        if not images:
            continue

        data = data.iloc[valid_indices]

        # WICHTIG: Als uint8 (1 Byte statt 4 Byte) speichern -> verhindert RAM-Absturz!
        X_arr = np.array(images, dtype=np.uint8)
        Y_arr = data[['speed', 'steering_angle']].to_numpy(dtype=np.float32)

        # Horizontal spiegeln entlang der Bildbreite (Achse 2 bei Shape [N, H, W, C])
        X_flipped = X_arr[:, :, ::-1, :].copy()
        Y_flipped = Y_arr.copy()
        Y_flipped[:, 1] = 180.0 - Y_flipped[:, 1]

        X.append(X_arr)
        X.append(X_flipped)
        Y.append(Y_arr)
        Y.append(Y_flipped)

    if not X:
        raise ValueError('Keine verwendbaren Daten gefunden.')

    X = np.concatenate(X, axis=0)
    Y = np.concatenate(Y, axis=0)

    print(f'Gesamte Bilder im RAM: {len(X)} (Speicherbedarf: {X.nbytes / (1024**3):.2f} GiB)')

    return X, Y, image_height, image_width, image_dimension, [sf.name for sf in sub_folders]


def write_txt(txt_path, lines, mode='a'):
    """Schreibt Zeilen in die Parameter-/Ergebnis-.txt."""
    with open(txt_path, mode, encoding='utf-8') as f:
        f.write('\n'.join(lines) + '\n')


def augment_live(image, label):
    """Wendet Helligkeit und Kontrast live während des Trainings an (kostet 0 Byte extra RAM)."""
    image = tf.cast(image, tf.float32)
    image = tf.image.random_brightness(image, max_delta=20.0)
    image = tf.image.random_contrast(image, lower=0.8, upper=1.2)
    image = tf.clip_by_value(image, 0.0, 255.0)
    return image, label


def train_deepcar(data_path=Path(r'C:\Users\LtCHa\Desktop\Coding\DeepCar\Training_Data'),
                  epochs=100,
                  learning_rate=0.0001,
                  batch_size=32,
                  train_split=0.70,
                  validate_split=0.15,
                  patience=8,
                  min_delta=0.01):

    data_path = Path(data_path)

    X, Y, fig_heigt, fig_width, fig_dim, loaded_folders = import_data(data_path=data_path)

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
        'clean_filter: drop_duplicates + stuck_steering_filter',
        f'epochs (max): {epochs}',
        f'learning_rate: {learning_rate}',
        f'batch_size: {batch_size}',
        f'train_split: {train_split}',
        f'validate_split: {validate_split}',
        f'test_split: {round(1 - train_split - validate_split, 2)}',
        f'early_stopping patience: {patience}',
        f'early_stopping min_delta: {min_delta}',
        'augmentation: horizontal flip (180 - angle) + live brightness (+/- 20) & contrast (0.8 - 1.2)',
        'color_space: BGR (OpenCV native)',
        'cropping: top=60, bottom=40, left=0, right=0',
    ], mode='w')

    # 1. Plot: Pairplot (Speed & Steering Angle Verteilung)
    data_df = pd.DataFrame(Y, columns=['speed', 'steering_angle'])
    g = sns.pairplot(data=data_df[['speed', 'steering_angle']])
    g.savefig(save_path / 'pairplot_speed_steering.png', dpi=150)
    plt.close('all')

    # Mischen
    indices = np.random.permutation(len(X))
    X = X[indices]
    Y = Y[indices]

    n = len(X)
    n_train = int(train_split * n)
    n_validate = int(validate_split * n)

    # Datenumfang in .txt
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

    # Direkt über Slicing aufteilen (spart extrem viel TensorFlow-Speicher!)
    train_ds = tf.data.Dataset.from_tensor_slices((X[:n_train], Y[:n_train]))
    validate_ds = tf.data.Dataset.from_tensor_slices((X[n_train:n_train + n_validate], Y[n_train:n_train + n_validate]))
    test_ds = tf.data.Dataset.from_tensor_slices((X[n_train + n_validate:], Y[n_train + n_validate:]))

    # CNN Architektur
    model = Sequential([
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
    ], name='DeepCar_CNN')

    model.compile(
        optimizer=tf.keras.optimizers.Adam(learning_rate=learning_rate),
        loss='mse',
        metrics=['mae']
    )

    model.summary()

    summary_lines = []
    model.summary(print_fn=lambda line, **kwargs: summary_lines.append(line))
    write_txt(txt_path, ['', '[Modell]', *summary_lines])

    # Live-Augmentation nur auf Trainingsdaten anwenden
    train_ds = (
        train_ds
        .shuffle(2000)
        .map(augment_live, num_parallel_calls=tf.data.AUTOTUNE)
        .batch(batch_size)
        .prefetch(tf.data.AUTOTUNE)
    )
    validate_ds = validate_ds.batch(batch_size).prefetch(tf.data.AUTOTUNE)
    test_ds = test_ds.batch(batch_size).prefetch(tf.data.AUTOTUNE)

    callbacks = [
        EarlyStopping(monitor='val_loss', min_delta=min_delta, patience=patience, restore_best_weights=True, verbose=1),
        ModelCheckpoint(filepath=str(save_path / 'best_deepcar.keras'), monitor='val_loss', save_best_only=True)
    ]

    print("Starte Training...")
    history = model.fit(train_ds, validation_data=validate_ds, epochs=epochs, callbacks=callbacks)

    # Vollständiges Keras-Modell speichern
    model.save(save_path / 'deepcar.keras')

    # Auswertung auf den Testdaten
    test_loss, test_mae = model.evaluate(test_ds)
    print(f'Test-MSE: {test_loss:.4f}  Test-MAE: {test_mae:.4f}')

    y_true = Y[n_train + n_validate:]
    y_pred = model.predict(test_ds, verbose=0)
    y_mean = Y[:n_train].mean(axis=0)

    mae_per_output = np.abs(y_pred - y_true).mean(axis=0)
    mae_baseline = np.abs(y_mean - y_true).mean(axis=0)
    var_y = y_true.var(axis=0)
    # Schutz gegen Division durch Null falls speed konstant 30 ist (Varianz = 0)
    r2 = np.where(var_y > 1e-6, 1 - ((y_pred - y_true)**2).mean(axis=0) / var_y, 0.0)

    print('MAE  [speed, steering]:', mae_per_output)
    print('Basis-MAE (nur Mittelwert):', mae_baseline)
    print('R² [speed, steering]:', r2)

    # TFLite Export in den Trainingsordner UND in den Hauptordner
    converter = tf.lite.TFLiteConverter.from_keras_model(model)
    converter.optimizations = [tf.lite.Optimize.DEFAULT]
    tflite_bytes = converter.convert()

    tflite_path = save_path / 'deepcar.tflite'
    tflite_path.write_bytes(tflite_bytes)
    Path('deepcar.tflite').write_bytes(tflite_bytes)

    # Kontrolle: Keras vs. TFLite an einem Testbild
    interp = tf.lite.Interpreter(model_path=str(tflite_path))
    interp.allocate_tensors()
    inp, out = interp.get_input_details()[0], interp.get_output_details()[0]
    x_sample = X[n_train + n_validate : n_train + n_validate + 1].astype(np.float32)
    y_sample = Y[n_train + n_validate]
    interp.set_tensor(inp['index'], x_sample)
    interp.invoke()
    keras_out = model.predict(x_sample, verbose=0)[0]
    tflite_out = interp.get_tensor(out['index'])[0]
    print('Keras :', keras_out)
    print('TFLite:', tflite_out)
    print('Soll  :', y_sample)

    # Ergebnisse in parameter.txt sichern
    h = history.history
    ep = np.arange(1, len(h['loss']) + 1)
    best = int(np.argmin(h['val_loss'])) + 1
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
        f'Kontrolle Soll  : {y_sample}',
    ])

    # 2. Plot: Trainingsverlauf (Loss & MAE)
    fig, ax = plt.subplots(1, 2, figsize=(13, 4.5))
    ax[0].plot(ep, h['loss'], label='Training')
    ax[0].plot(ep, h['val_loss'], label='Validierung')
    ax[0].axvline(best, color='gray', ls='--', label=f'beste Epoche {best}')
    ax[0].set_yscale('log')
    ax[0].set(title='Loss (MSE)', xlabel='Epoche', ylabel='MSE')
    ax[0].legend()
    ax[0].grid(alpha=0.3)

    ax[1].plot(ep, h['mae'], label='Training')
    ax[1].plot(ep, h['val_mae'], label='Validierung')
    ax[1].axvline(best, color='gray', ls='--')
    ax[1].set(title='MAE', xlabel='Epoche', ylabel='MAE')
    ax[1].legend()
    ax[1].grid(alpha=0.3)
    plt.tight_layout()
    fig.savefig(save_path / 'training_loss_mae.png', dpi=150)
    plt.close(fig)

    # 3. Plot: Vorhersage vs. Echt & Fehlerverteilung
    names = ['speed', 'steering_angle']
    fig, ax = plt.subplots(2, 2, figsize=(13, 9))
    for i, name in enumerate(names):
        lim = [y_true[:, i].min(), y_true[:, i].max()]
        ax[0, i].scatter(y_true[:, i], y_pred[:, i], s=8, alpha=0.5)
        ax[0, i].plot(lim, lim, 'r--', label='ideal')
        ax[0, i].set(title=f'{name}: Vorhersage vs. echt', xlabel='echt', ylabel='vorhergesagt')
        ax[0, i].legend()
        ax[0, i].grid(alpha=0.3)

        ax[1, i].hist(y_pred[:, i] - y_true[:, i], bins=50)
        ax[1, i].set(title=f'{name}: Fehlerverteilung', xlabel='Fehler')
        ax[1, i].grid(alpha=0.3)

    plt.tight_layout()
    fig.savefig(save_path / 'test_vorhersage_vs_echt.png', dpi=150)
    plt.close(fig)

    # 4. Plot: Lenkwinkel-Verlauf über die Testbilder
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
    train_deepcar()