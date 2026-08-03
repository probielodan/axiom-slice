import React, { useState, useEffect, useRef } from 'react';

const App = () => {
    const [isLoading, setIsLoading] = useState(true);
    const [models, setModels] = useState([]);
    const [projectName, setProjectName] = useState('');
    const [slicerOptions, setSlicerOptions] = useState([]);
    const [commandOptions, setCommandOptions] = useState([]);
    const [selectedConfigs, setSelectedConfigs] = useState({
        filament: '',
        machine: '',
        process: '',
    });
    const [configOptions, setConfigOptions] = useState([
        { machine: [] },
        { filament: [] },
        { process: [] },
    ]);
    const [isSlicing, setIsSlicing] = useState(false);
    const [log, setLog] = useState([]);
    const logContainerRef = useRef(null);
    const fileUploadRef = useRef(null);
    const [error, setError] = useState(null);
    const [configError, setConfigError] = useState(null);
    const ansiColorMap = {
        '31': 'text-red-500',   // RED
        '32': 'text-green-500', // GREEN
        '33': 'text-yellow-500',// YELLOW
        '34': 'text-blue-400',  // BLUE
        '36': 'text-cyan-400',  // CYAN
        '0': 'text-gray-400',   // Reset (No Color)
    };
    const [dark, setDark] = useState(
        window.matchMedia("(prefers-color-scheme: dark)").matches
    );

    useEffect(() => {
        const mediaQuery = window.matchMedia("(prefers-color-scheme: dark)");
        const handleChange = (e) => setDark(e.matches);

        mediaQuery.addEventListener("change", handleChange);
        return () => mediaQuery.removeEventListener("change", handleChange);
    }, []);

    useEffect(() => {
        const fetchState = async () => {
            try {
                const configResponse = await fetch('/api/configs');
                if (!configResponse.ok) {
                    throw new Error('Failed to fetch configurations.');
                }
                const configData = await configResponse.json();

                const stateResponse = await fetch('/api/slicer-state');
                if (!stateResponse.ok) {
                    throw new Error('Failed to fetch slicer state.');
                }
                const slicerState = await stateResponse.json();

                let finalSelectedConfigs = { ...slicerState.selectedConfigs };

                configData.map((configObject) => {
                    const [configType, availableOptions] = Object.entries(configObject)[0]
                    if (slicerState.selectedConfigs) {
                        const savedValue = slicerState.selectedConfigs[configType];
                        if (!savedValue || !availableOptions.includes(savedValue)) {
                            finalSelectedConfigs = {
                                ...finalSelectedConfigs,
                                [configType]: availableOptions[0] || '',
                            };
                        }
                    }
                })

                setConfigOptions(configData);
                setSelectedConfigs(finalSelectedConfigs);
                setSlicerOptions(slicerState.slicerOptions);
                setCommandOptions(slicerState.commandOptions);
            } catch (err) {
                console.error('Fetch error:', err);
                setConfigError(`${err.message}`);
            } finally {
                setIsLoading(false);
            }
        };
        fetchState();
    }, []);

    useEffect(() => {
        if (logContainerRef.current) {
            logContainerRef.current.scrollTop = logContainerRef.current.scrollHeight;
        }
    }, [log]);

    const saveSettings = async (dataToSave) => {
        try {
            const response = await fetch('/api/slicer-state', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(dataToSave),
            });

            if (!response.ok) {
                throw new Error('Failed to save settings.');
            }
        } catch (err) {
            console.error('Save error:', err);
        }
    };

    const handleConfigInputChange = (e) => {
        const { id, value } = e.target;
        setSelectedConfigs(prevOptions => {
            const updated = {
                ...prevOptions,
                [id]: value,
            }
            saveSettings({ selectedConfigs: updated })
            return updated;
        });
    };

    const updateSectionSetting = (sections, sectionName, settingName, value) => {
        return sections.map(section => {
            if (section.section === sectionName) {
                const updatedSettings = section.settings.map(setting => {
                    if (setting.setting === settingName) {
                        if (setting.type === 'integer') return { ...setting, value: parseInt(value) };
                        if (setting.type === 'float') return { ...setting, value: parseFloat(value) };
                        return { ...setting, value: value };
                    }
                    return setting;
                });
                return { ...section, settings: updatedSettings };
            }
            return section;
        });
    };

    const handleCommandInputChange = (sectionName, settingName, value) => {
        setCommandOptions(prev => {
            const updated = updateSectionSetting(prev, sectionName, settingName, value)
            saveSettings({ commandOptions: updated })
            return updated
        });
    };

    const handleSlicerInputChange = (sectionName, settingName, value) => {
        setSlicerOptions(prev => updateSectionSetting(prev, sectionName, settingName, value));
    };

    const parseAnsiCodes = (rawMessage) => {
        let cleanMessage = rawMessage;
        let colorClass = 'text-gray-400';
        /* eslint-disable no-control-regex */
        const ansiRegex = /\x1b\[(\d+)m/g;
        const match = ansiRegex.exec(rawMessage);

        if (match) {
            const code = match[1];
            if (ansiColorMap[code]) {
                colorClass = ansiColorMap[code];
            }
        }
        cleanMessage = rawMessage.replace(ansiRegex, '');
        return { cleanMessage, colorClass };
    };

    const output = (message) => {
        const { cleanMessage, colorClass } = parseAnsiCodes(message);
        setLog((prevLog) => [...prevLog, { message: cleanMessage, color: colorClass }]);
    };

    const appendCommandOptions = (formData, options) => {
        if (options == selectedConfigs) {
            for (const key in options) {
                formData.append(key, options[key]);
            }
        } else {
            let slicer_args = []
            options.forEach(section => {
                section.settings.forEach(setting => {
                    if (options == commandOptions) {
                        formData.append(setting.setting, setting.value);
                    } else {
                        if (setting.type === 'boolean') {
                            if (setting.value) {
                                slicer_args.push(`--${setting.setting.replaceAll("_", "-")}`);
                            }
                        } else {
                            slicer_args.push(`--${setting.setting.replaceAll("_", "-") }`, setting.value);
                        }
                    }
                });
            });
            if (slicer_args.length > 0) {
                formData.append('slicer_args', JSON.stringify(slicer_args));
            }
        }
    };

    const handleSlice = async (e) => {
        e.preventDefault();
        setLog([]);
        setError(null);
        setIsSlicing(true);

        const formData = new FormData();
        models.forEach(model => formData.append('models', model));
        formData.append('project_name', projectName);
        appendCommandOptions(formData, selectedConfigs);
        appendCommandOptions(formData, commandOptions);
        appendCommandOptions(formData, slicerOptions);

        try {
            const response = await fetch('/api/slice', {
                method: 'POST',
                body: formData,
            });

            if (!response.ok) {
                const errorData = await response.json();
                setError(`Server Error: ${errorData.error}`);
                setIsSlicing(false);
                return;
            }

            const reader = response.body.getReader();
            const decoder = new TextDecoder();
            let done = false;

            while (!done) {
                const { value, done: readerDone } = await reader.read();
                done = readerDone;
                const chunk = decoder.decode(value);
                const newLines = chunk.split('\n').filter(line => line.length > 0);
                newLines.forEach(line => output(line));
            }

        } catch (err) {
            console.error('Slice error:', err);
            setError('Failed to connect to the slicing server. Is it running?');
        } finally {
            setIsSlicing(false);
        }
    };

    const handleDragOver = (e) => {
        e.preventDefault();
    };

    const handleDrop = (e) => {
        e.preventDefault();
        setModels(Array.from(e.dataTransfer.files));
    };

    const findSetting = (options, settingName) => {
        for (const section of options) {
            return section.settings.find(setting => setting.setting === settingName);
        }
    };

    const renderConfigOptions = () => {
        return configOptions.map((configObject) => {
            const [configType, options] = Object.entries(configObject)[0];

            return (
                <div key={configType}>
                    <label
                        htmlFor={configType}
                        className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1"
                    >
                        {configType.charAt(0).toUpperCase() + configType.slice(1)}
                    </label>
                    <select
                        id={configType}
                        value={selectedConfigs[configType]}
                        onChange={handleConfigInputChange}
                        className=" -indigo-500 transition duration-200"
                    >
                        {options && options.length > 0 ? (
                            options.map(option => (
                                <option key={option} value={option}>
                                    {option}
                                </option>
                            ))
                        ) : (
                            <option disabled>No {configType}s found</option>
                        )}
                    </select>
                </div>
            );
        });
    };

    const renderOptions = (optionsObject, changeHandler) => {
        return optionsObject.map(section => (
            <div key={section.section} className="mb-4">
                {optionsObject.length > 1 && <h3 className="mb-2 text-lg font-semibold text-gray-700 dark:text-gray-300">{section.section}</h3>}
                <div className="grid grid-cols-1 sm:grid-cols-2 gap-x-4 gap-y-2">
                    {section.settings.map((argConfig) => {
                        const printerIpSetting = findSetting(optionsObject, 'printer');
                        const { type, label, helpText, options, value, min, max, setting, placeholder } = argConfig;
                        const isDisabled = setting == "start_print" && (!printerIpSetting || !printerIpSetting.value);

                        switch (type) {
                            case 'boolean':

                                if (isDisabled && value) {
                                    changeHandler(section.section, setting, false);
                                }

                                return (
                                    <div key={setting} title={helpText}>
                                        <label
                                            htmlFor={setting}
                                            className="block text-sm font-medium text-gray-700 dark:text-gray-300"
                                        >
                                            {label}
                                        </label>
                                        <button
                                            type="button"
                                            onClick={() => !isDisabled && changeHandler(section.section, setting, !value)}
                                            disabled={isDisabled}
                                            className={`w-full h-8 rounded-lg border transition duration-200
                                                ${isDisabled
                                                    ? 'bg-gray-200 dark:bg-gray-600 border-gray-300 dark:border-gray-500 cursor-not-allowed opacity-50'
                                                    : value
                                                        ? 'bg-indigo-500 border-indigo-600 hover:bg-indigo-300 dark:hover:bg-indigo-700'
                                                        : 'text-gray-700 border-gray-300 dark:bg-gray-700 dark:text-gray-300 dark:border-gray-600 hover:bg-indigo-200 dark:hover:bg-indigo-800'
                                                }`}
                                        >
                                            <div className={`bg-white dark:bg-gray-200 w-2/3 h-full rounded-lg shadow-md transform transition-transform duration-200 ${value ? 'translate-x-1/2' : ''}`} />
                                        </button>
                                    </div>
                                );
                            case 'integer':
                            case 'float':
                                return (
                                    <div key={setting} title={helpText}>
                                        <label htmlFor={setting} className="block text-sm font-medium text-gray-700 dark:text-gray-300">
                                            {label}
                                        </label>
                                        <input
                                            type="number"
                                            id={setting}
                                            value={value}
                                            onChange={e => changeHandler(section.section, setting, e.target.value)}
                                            min={min}
                                            max={max}
                                            step={type === 'float' ? 'any' : '1'}
                                            className="w-full px-4 py-1 rounded-lg border border-gray-300 dark:border-gray-600 dark:bg-gray-700 dark:text-gray-300 hover:ring-2 hover:ring-indigo-400 dark:hover:ring-indigo-500 transition duration-200"
                                        />
                                    </div>
                                );
                            case 'enumeration':
                                return (
                                    <div key={setting} title={helpText}>
                                        <label htmlFor={setting} className="block text-sm font-medium text-gray-700 dark:text-gray-300">
                                            {label}
                                        </label>
                                        <select
                                            id={setting}
                                            value={value}
                                            onChange={e => changeHandler(section.section, setting, e.target.value)}
                                            className="w-full px-4 py-1 rounded-lg border border-gray-300 dark:border-gray-600 dark:bg-gray-700 dark:text-gray-300 hover:ring-2 hover:ring-indigo-400 dark:hover:ring-indigo-500 transition duration-200"
                                        >
                                            {options.map(option => (
                                                <option key={option.value} value={option.value}>{option.label}</option>
                                            ))}
                                        </select>
                                    </div>
                                );
                            case 'string':
                                return (
                                    <div key={setting} title={helpText}>
                                        <label htmlFor={setting} className="block text-sm font-medium text-gray-700 dark:text-gray-300">
                                            {label}
                                        </label>
                                        <input
                                            type="text"
                                            id={setting}
                                            value={value}
                                            onChange={e => changeHandler(section.section, setting, e.target.value)}
                                            className="w-full px-4 py-1 rounded-lg border border-gray-300 dark:border-gray-600 dark:bg-gray-700 dark:text-gray-300 hover:ring-2 hover:ring-indigo-400 dark:hover:ring-indigo-500 transition duration-200"
                                            placeholder={placeholder ? placeholder : ""}
                                        />
                                    </div>
                                );
                            default:
                                return null;
                        }
                    })}
                </div>
            </div>
        ));
    };

    return (
        <div className={dark ? "dark" : ""}>
            <div className="min-h-screen bg-gray-100 dark:bg-gray-900 p-4 md:p-8 flex items-center justify-center font-sans">
                {isLoading ? <h1 className="text-5xl font-extrabold text-gray-700 dark:text-gray-300">Loading...</h1> :
                    <div className="bg-white dark:bg-gray-800 p-3 md:p-8 rounded-2xl shadow-xl w-full border border-gray-200 dark:border-gray-700">
                        <h1 className="text-4xl font-extrabold text-center text-gray-800 dark:text-gray-300 mb-2">slice.py UI</h1>
                        <p className="text-center text-gray-700 dark:text-gray-300 mb-8">
                            A web interface for your OrcaSlicer automation script.
                        </p>

                        <form onSubmit={handleSlice} className="grid grid-cols-1 lg:grid-cols-2 gap-8">
                            {/* Slicing Configuration Column */}
                            <div className="flex flex-col gap-6">
                                <div className="flex flex-col gap-2 bg-gray-50 dark:bg-gray-900 p-4 md:p-6 rounded-xl border border-gray-200 dark:border-gray-700 shadow-inner">
                                    <h2 className="text-xl font-bold text-gray-800 dark:text-gray-300 border-b border-gray-300 dark:border-gray-600 pb-1">Configuration</h2>
                                    <div>
                                        <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1" htmlFor="file-upload">
                                            Model File(s) <span className="text-red-500">*</span>
                                        </label>
                                        <div
                                            className="mt-1 flex justify-center px-6 pt-5 pb-6 border-2 border-gray-300 dark:border-gray-600 dark:bg-gray-700 border-dashed rounded-md hover:border-gray-400 dark:hover:border-gray-500 transition-colors duration-200 ease-in-out"
                                            onDragOver={handleDragOver}
                                            onDrop={handleDrop}
                                            onClick={() => fileUploadRef.current.click()}
                                        >
                                            <div className="space-y-1 text-center">
                                                <svg className="mx-auto h-12 w-12 fill-gray-400 dark:fill-gray-500" viewBox="0 0 486.3 486.3" aria-hidden="true">
                                                    <path d="M395.5,135.8c-5.2-30.9-20.5-59.1-43.9-80.5c-26-23.8-59.8-36.9-95-36.9c-27.2,0-53.7,7.8-76.4,22.5    c-18.9,12.2-34.6,28.7-45.7,48.1c-4.8-0.9-9.8-1.4-14.8-1.4c-42.5,0-77.1,34.6-77.1,77.1c0,5.5,0.6,10.8,1.6,16    C16.7,200.7,0,232.9,0,267.2c0,27.7,10.3,54.6,29.1,75.9c19.3,21.8,44.8,34.7,72,36.2c0.3,0,0.5,0,0.8,0h86    c7.5,0,13.5-6,13.5-13.5s-6-13.5-13.5-13.5h-85.6C61.4,349.8,27,310.9,27,267.1c0-28.3,15.2-54.7,39.7-69    c5.7-3.3,8.1-10.2,5.9-16.4c-2-5.4-3-11.1-3-17.2c0-27.6,22.5-50.1,50.1-50.1c5.9,0,11.7,1,17.1,3c6.6,2.4,13.9-0.6,16.9-6.9    c18.7-39.7,59.1-65.3,103-65.3c59,0,107.7,44.2,113.3,102.8c0.6,6.1,5.2,11,11.2,12c44.5,7.6,78.1,48.7,78.1,95.6    c0,49.7-39.1,92.9-87.3,96.6h-73.7c-7.5,0-13.5,6-13.5,13.5s6,13.5,13.5,13.5h74.2c0.3,0,0.6,0,1,0c30.5-2.2,59-16.2,80.2-39.6    c21.1-23.2,32.6-53,32.6-84C486.2,199.5,447.9,149.6,395.5,135.8z" />
                                                    <path d="M324.2,280c5.3-5.3,5.3-13.8,0-19.1l-71.5-71.5c-2.5-2.5-6-4-9.5-4s-7,1.4-9.5,4l-71.5,71.5c-5.3,5.3-5.3,13.8,0,19.1    c2.6,2.6,6.1,4,9.5,4s6.9-1.3,9.5-4l48.5-48.5v222.9c0,7.5,6,13.5,13.5,13.5s13.5-6,13.5-13.5V231.5l48.5,48.5    C310.4,285.3,318.9,285.3,324.2,280z" />
                                                </svg>

                                                <div className="flex justify-center text-sm text-gray-700 dark:text-gray-300">
                                                    <label
                                                        htmlFor="file-upload"
                                                        className="relative cursor-pointer bg-transparent rounded-md font-medium text-indigo-600 dark:text-indigo-400 hover:text-indigo-500 focus-within:outline-none"
                                                    >
                                                        <span>Upload a file</span>
                                                        <input
                                                            ref={fileUploadRef}
                                                            id="file-upload"
                                                            name="file-upload"
                                                            type="file"
                                                            multiple
                                                            onChange={(e) => setModels(Array.from(e.target.files))}
                                                            className="sr-only"
                                                        />
                                                    </label>
                                                    <p className="pl-1">or drag and drop</p>
                                                </div>
                                                {models.length > 0 ? (
                                                    <p className="text-xs text-gray-500 dark:text-gray-400 mt-2">
                                                        Selected: {models.map((f) => f.name).join(', ')}
                                                    </p>
                                                ) : <p className="text-xs text-gray-500 dark:text-gray-400">.stl, .3mf</p>
                                                }
                                            </div>
                                        </div>
                                    </div>

                                    <div>
                                        <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">
                                            Project Name
                                        </label>
                                        <input
                                            type="text"
                                            id="projectName"
                                            value={projectName}
                                            onChange={(e) => setProjectName(e.target.value)}
                                            className="w-full px-4 py-1 rounded-lg border border-gray-300 dark:border-gray-600 dark:bg-gray-700 dark:text-gray-300 hover:ring-2 hover:ring-indigo-400 dark:hover:ring-indigo-500 transition duration-200"
                                        />
                                    </div>

                                    {/* Conditional rendering for configs based on loading state */}
                                    {configError ? (
                                        <div className="bg-red-100 dark:bg-red-900 border border-red-400 dark:border-red-700 text-red-700 dark:text-red-200 p-4 rounded-xl mb-4" role="alert">
                                            <span className="font-bold">Error:</span> {configError}
                                        </div>
                                    ) : (renderConfigOptions())}
                                </div>
                                <div className="flex flex-col gap-2 bg-gray-50 dark:bg-gray-900 p-4 md:p-6 rounded-xl border border-gray-200 dark:border-gray-700 shadow-inner">
                                    <h2 className="text-xl font-bold text-gray-800 dark:text-gray-300 border-b border-gray-300 dark:border-gray-600 pb-1">Slicer Options</h2>
                                    {renderOptions(slicerOptions, handleSlicerInputChange)}
                                    <button
                                        type="button"
                                        onClick={() => saveSettings({ slicerOptions: slicerOptions })}
                                        className="w-full px-6 py-3 rounded-xl font-bold bg-indigo-500 hover:bg-indigo-600 text-gray-100 transition duration-200"
                                    >
                                        Save as Defaults
                                    </button>
                                </div>
                                <div className="flex flex-col gap-2 bg-gray-50 dark:bg-gray-900 p-4 md:p-6 rounded-xl border border-gray-200 dark:border-gray-700 shadow-inner">
                                    <h2 className="text-xl font-bold text-gray-800 dark:text-gray-300 border-b border-gray-300 dark:border-gray-600 pb-1">Printer & Script Options</h2>
                                    {renderOptions(commandOptions, handleCommandInputChange)}
                                </div>
                            </div>

                            {/* Slicing Log Column */}
                            <div className="flex flex-col gap-6">
                                <div ref={logContainerRef} className="bg-gray-50 dark:bg-gray-900 p-6 rounded-xl flex-grow h-96 overflow-y-auto border border-gray-200 dark:border-gray-700 shadow-inner">
                                    <h2 className="text-xl font-bold text-gray-800 dark:text-gray-300 border-b border-gray-300 dark:border-gray-600 pb-1 mb-4">Slicing Log</h2>
                                    {log.map((entry, index) => (
                                        <p key={index} className={`font-mono text-sm ${entry.color}`}>
                                            {entry.message}
                                        </p>
                                    ))}
                                    {isSlicing && (
                                        <p className="font-mono text-sm text-blue-400 animate-pulse">Processing...</p>
                                    )}
                                </div>
                                {error && (
                                    <div className="bg-red-100 dark:bg-red-900 border border-red-400 dark:border-red-700 text-red-700 dark:text-red-200 p-4 rounded-xl" role="alert">
                                        <span className="font-bold">Error:</span> {error}
                                    </div>
                                )}
                                <button
                                    type="submit"
                                    disabled={isSlicing || models.length === 0}
                                    className={`w-full py-3 px-6 rounded-full font-bold text-lg text-gray-100 transition duration-300 ease-in-out transform hover:scale-105 shadow-lg
                                        ${isSlicing || models.length === 0
                                            ? 'bg-gray-400 cursor-not-allowed dark:bg-gray-600'
                                            : 'bg-indigo-600 hover:bg-indigo-700'
                                        }`}
                                >
                                    {isSlicing ? 'Slicing...' : 'Start Slicing'}
                                </button>
                            </div>
                        </form>
                    </div>}
            </div>
        </div>
    );
};

export default App;
