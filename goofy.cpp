#include <bits/stdc++.h>
#include <filesystem>
using namespace std;
namespace fs = std::filesystem;

int main() {
    cout << "=== C++ File Madness Script ===\n";

    string folder = "cpp_madness";
    cout << "Creating folder '" << folder << "'...\n";
    fs::create_directory(folder);

    // Create 3 files with random content
    for (int i = 1; i <= 3; ++i) {
        string filename = folder + "/file" + to_string(i) + ".txt";
        ofstream fout(filename);
        fout << "This is file " << i << " created by your C++ madness script.\n";
        fout << "Random number: " << rand() % 1000 << "\n";
        fout.close();
        cout << "Created " << filename << "\n";
    }

    this_thread::sleep_for(chrono::milliseconds(800));

    // List files
    cout << "\nListing files:\n";
    for (auto &p : fs::directory_iterator(folder)) {
        cout << "  - " << p.path().filename() << "\n";
    }

    // Move file1.txt -> moved_file1.txt
    fs::rename(folder + "/file1.txt", folder + "/moved_file1.txt");
    cout << "\nMoved file1.txt -> moved_file1.txt\n";

    // Delete file2.txt
    fs::remove(folder + "/file2.txt");
    cout << "Deleted file2.txt\n";

    // Append content to file3.txt
    ofstream fout(folder + "/file3.txt", ios::app);
    fout << "Appending some madness content!\n";
    fout.close();
    cout << "Appended content to file3.txt\n";

    cout << "\nFinal folder contents:\n";
    for (auto &p : fs::directory_iterator(folder)) {
        cout << "  - " << p.path().filename() << "\n";
    }

    cout << "\nCleanup: removing folder...\n";
    fs::remove_all(folder);

    cout << "All done. Folder cleaned up!\n";

    return 0;
}
