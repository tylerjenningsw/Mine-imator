#include "Automation.hpp"
#include "Generated/Scripts.hpp"

#include "Asset/Surface.hpp"
#include "Render/GraphicsApiHandler.hpp"

#include <QJsonArray>
#include <QJsonDocument>
#include <QJsonObject>
#include <QSaveFile>

namespace CppProject::Automation
{
	static QString jobFile;
	static QString resultFile;
	static QString workDirectory;

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

		QSaveFile file(filename.QStr());
		if (!file.open(QIODevice::WriteOnly) || !image.save(&file, "PNG"))
		{
			file.cancelWriting();
			return false;
		}
		if (!file.commit())
			return false;

		QImage check(filename.QStr(), "PNG");
		return !check.isNull() && check.size() == image.size();
	}
}
