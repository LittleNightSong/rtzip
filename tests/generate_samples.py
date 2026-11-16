from pathlib import Path

parent = Path(__file__).parent

if parent.name == 'tests':
    parent = parent.parent

samples = parent / 'samples'
samples.mkdir(parents=True, exist_ok=True)


def generate_zip64_too_many_files():  # TODO: do not work
    import zipfile

    with zipfile.ZipFile(samples / 'zip64_too_many_files.zip', 'w') as zf:
        for i in range(100000):
            data = i.to_bytes(8)
            zf.writestr(f'file_{i}', data)


def generate_zip64_one_big_file():
    import zipfile

    with zipfile.ZipFile(samples / 'zip64_one_big_file.zip', 'w') as zf:
        with zf.open('big_4gb', 'w', force_zip64=True) as f:
            data_1mb = b'\01' * 1024 * 1024
            for i in range(4):  # 4 * 1024 * 1mb = 4gb
                for j in range(1024):
                    f.write(data_1mb)


def generate_zip64_offset_overflow():
    import zipfile

    with zipfile.ZipFile(samples / 'zip64_offset_overflow.zip', 'w') as zf:
        for n in range(2):
            with zf.open(f'offset_overflow-{n}', 'w', force_zip64=True) as f:
                data_1mb = b'\01' * 1024 * 1024
                for i in range(2):  # 2 * 1024 * 1mb = 2gb
                    for j in range(1024):
                        f.write(data_1mb)

        with zf.open('over', 'w') as f:
            f.write(data_1mb)


def generate():
    print("创建 zip64 文件：数量超出范围（Zip64 EOCD）......")
    generate_zip64_too_many_files()
    print("创建 zip64 文件：单文件大小超出范围（Zip64 EOCD）......")
    generate_zip64_one_big_file()
    print("创建 zip64 文件：偏移量超出范围（Zip64 EOCD）......")
    generate_zip64_offset_overflow()


if __name__ == '__main__':
    generate()
