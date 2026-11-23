#include "Automation.hpp"
#include "Generated/Scripts.hpp"

#include "Asset/Surface.hpp"
#include "Render/GraphicsApiHandler.hpp"

#include <QJsonArray>
#include <QJsonDocument>
#include <QJsonObject>
#include <QSaveFile>
#include <QTemporaryDir>
#include <QTemporaryFile>

namespace CppProject::Automation
{
	static QString jobFile;
	static QString resultFile;
	static QString workDirectory;

	static QString CanonicalPath(const QString& path)
	{
		if (path.isEmpty() || !QDir::isAbsolutePath(path))
			return "";

		QFileInfo info(QDir::fromNativeSeparators(path));
		QStringList missing;
		while (!info.exists())
		{
			if (info.isSymLink())
				return "";
			QString parent = info.dir().absolutePath();
			if (parent == info.absoluteFilePath())
				return "";
			missing.prepend(info.fileName());
			info.setFile(parent);
		}

		QString resolved = info.canonicalFilePath();
		if (resolved.isEmpty())
			return "";
		for (const QString& part : missing)
			resolved = QDir(resolved).filePath(part);
		return QDir::cleanPath(QDir::fromNativeSeparators(resolved));
	}

	static QString StagePath(const QString& destination, bool directory)
	{
		QFileInfo target(destination);
		if (!QDir().mkpath(target.absolutePath()))
			return "";
		QString pattern = target.absolutePath() + "/.mineimator-XXXXXX";
		if (directory)
		{
			QTemporaryDir stage(pattern);
			stage.setAutoRemove(false);
			return stage.isValid() ? stage.path() : QString();
		}
		QTemporaryFile stage(pattern);
		stage.setAutoRemove(false);
		return stage.open() ? stage.fileName() : QString();
	}

	static bool Publish(const QString& staged, const QString& destination, bool overwrite)
	{
		if (QFileInfo(staged).isDir())
			return !QFileInfo::exists(destination) && QDir().rename(staged, destination);
		if (!overwrite)
			return QFile::rename(staged, destination);

		QFile source(staged);
		QSaveFile target(destination);
		if (!source.open(QIODevice::ReadOnly) || !target.open(QIODevice::WriteOnly))
			return false;
		while (!source.atEnd())
		{
			QByteArray data = source.read(65536);
			if (source.error() != QFile::NoError || target.write(data) != data.size())
			{
				target.cancelWriting();
				return false;
			}
		}
		if (!target.commit())
			return false;
		source.close();
		QFile::remove(staged);
		return true;
	}

	static QString CleanAbsolute(const QString& path)
	{
		if (path.isEmpty() || !QDir::isAbsolutePath(path))
			return "";
		return QDir::cleanPath(QDir::fromNativeSeparators(path));
	}

	static void Refuse(const QString& reason)
	{
		std::cerr << "Mine-imator automation refused: " << reason.toStdString() << std::endl;
		std::exit(3);
	}

	void Init()
	{
		QString job = qEnvironmentVariable("MINEIMATOR_AUTOMATION_JOB");
		QString result = qEnvironmentVariable("MINEIMATOR_AUTOMATION_RESULT");
		if (job.isEmpty() && result.isEmpty())
			return;

		job = CleanAbsolute(job);
		result = CleanAbsolute(result);
		if (job.isEmpty() || result.isEmpty())
			Refuse("MINEIMATOR_AUTOMATION_JOB and MINEIMATOR_AUTOMATION_RESULT must both be absolute paths");
		if (!QFileInfo(job).isFile())
			Refuse("job file not found: " + job);
		if (QFileInfo::exists(result))
			Refuse("result file already exists: " + result);
		if (QString::compare(job, result, Qt::CaseInsensitive) == 0)
			Refuse("job and result paths must differ");

		QString work = result + ".work/";
		if (QFileInfo::exists(work))
			Refuse("work directory already exists: " + work);
		if (!QDir().mkpath(work + "tmp"))
			Refuse("could not create work directory: " + work);

		jobFile = job;
		resultFile = result;
		workDirectory = work;
	}

	bool Active()
	{
		return !jobFile.isEmpty();
	}

	QString WorkDirectory()
	{
		return workDirectory;
	}

	QString LogFile()
	{
		return workDirectory + "log.txt";
	}

	static bool WriteResult(const QByteArray& data)
	{
		if (QFileInfo::exists(resultFile))
			return false;

		QSaveFile file(resultFile);
		if (!file.open(QIODevice::WriteOnly))
			return false;
		if (file.write(data) != data.size())
		{
			file.cancelWriting();
			return false;
		}
		return file.commit();
	}

	bool ReportFatal(const QString& message)
	{
		if (!Active())
			return false;

		Printer::Line("[AUTOMATION FATAL] " + message);
		if (QFileInfo::exists(resultFile))
			return true;

		QJsonValue id;
		QFile job(jobFile);
		if (job.open(QFile::ReadOnly))
		{
			QJsonObject root = QJsonDocument::fromJson(job.readAll()).object();
			if (root.value("id").isString())
				id = root.value("id");
		}

		QJsonObject error;
		error["code"] = "FATAL";
		error["message"] = message;

		QJsonObject root;
		root["schema"] = "mineimator.result/1";
		root["id"] = id;
		root["status"] = "error";
		root["error"] = error;
		root["steps"] = QJsonArray();
		root["artifacts"] = QJsonArray();
		WriteResult(QJsonDocument(root).toJson());
		return true;
	}
}

namespace CppProject
{
	StringType automation_path_canonical(StringType path)
	{
		return Automation::CanonicalPath(path.QStr());
	}

	StringType automation_stage_create(StringType destination, BoolType directory)
	{
		return Automation::StagePath(destination.QStr(), directory);
	}

	BoolType automation_publish(StringType staged, StringType destination, BoolType overwrite)
	{
		return Automation::Publish(staged.QStr(), destination.QStr(), overwrite);
	}

	StringType automation_json_string(StringType value)
	{
		QJsonArray array;
		array.append(value.QStr());
		QByteArray json = QJsonDocument(array).toJson(QJsonDocument::Compact);
		return QString::fromUtf8(json.mid(1, json.size() - 2));
	}

	StringType automation_job_file()
	{
		return Automation::jobFile;
	}

	StringType automation_result_file()
	{
		return Automation::resultFile;
	}

	BoolType automation_result_write(StringType json)
	{
		return Automation::WriteResult(json.QStr().toUtf8());
	}

	BoolType automation_surface_save(IntType id, StringType filename)
	{
		Surface* surf = FindSurface(id);
		if (!surf)
			return false;

		GFX->SubmitBatch();
		QImage image = surf->ToImage();

		QString staged = Automation::StagePath(filename.QStr(), false);
		if (staged.isEmpty())
			return false;
		QSaveFile file(staged);
		if (!file.open(QIODevice::WriteOnly) || !image.save(&file, "PNG"))
		{
			file.cancelWriting();
			QFile::remove(staged);
			return false;
		}
		if (!file.commit())
		{
			QFile::remove(staged);
			return false;
		}

		QImage check(staged, "PNG");
		bool ok = !check.isNull() && check.size() == image.size() &&
			Automation::Publish(staged, filename.QStr(), global::automation_overwrite);
		QFile::remove(staged);
		return ok;
	}
}
