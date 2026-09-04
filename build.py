import os
import zipfile

addon_name = "quickUISearch"
output_filename = f"{addon_name}.nvda-addon"

# Files and folders to include
files_to_include = ["manifest.ini", "buildVars.py", "README.md"]
folders_to_include = ["globalPlugins"]

def build_addon():
    print(f"Building {output_filename}...")
    
    if os.path.exists(output_filename):
        os.remove(output_filename)
        
    with zipfile.ZipFile(output_filename, 'w', zipfile.ZIP_DEFLATED) as zip_file:
        # Write single files
        for f in files_to_include:
            if os.path.exists(f):
                zip_file.write(f)
                print(f"Added file: {f}")
            else:
                print(f"Notice: {f} not found, skipping.")
                
        # Write directories
        for folder in folders_to_include:
            if os.path.exists(folder):
                for root, dirs, files in os.walk(folder):
                    for file in files:
                        if not file.endswith('.pyc') and '__pycache__' not in root:
                            filepath = os.path.join(root, file)
                            arcname = os.path.relpath(filepath, start=os.path.curdir)
                            zip_file.write(filepath, arcname)
                            print(f"Added file: {arcname}")
            else:
                print(f"Warning: folder {folder} not found.")
                
    print(f"Successfully created {output_filename} in the current directory!")

if __name__ == "__main__":
    build_addon()
