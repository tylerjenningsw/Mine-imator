#include "Generated/Scripts.hpp"

#include <QCoreApplication>
#include <QDateTime>
#include <QDir>
#include <QJsonDocument>
#include <QJsonObject>
#include <QPointer>
#include <QQueue>
#include <QRandomGenerator>
#include <QSaveFile>
#include <QStandardPaths>
#include <QTcpServer>
#include <QTcpSocket>

namespace CppProject
{
	IntType json_load_from_string(StringType json, IntType typeMapId);
}

namespace CppProject::Live
{
	static const qint64 MAX_LINE = 8 * 1024 * 1024;
	static const int MAX_QUEUE = 32;
	static const int MAX_CACHE = 64;
	static const qint64 BUSY_MS = 10000;

	struct Request
	{
		QString requestId;
		QByteArray body;
		qint64 received = 0;
		QList<QPointer<QTcpSocket>> waiters;
	};

	static QTcpServer* server = nullptr;
	static QByteArray token;
	static QString sessionId;
	static QString connectionFile;
	static QQueue<Request> queue;
	static Request current;
	static bool hasCurrent = false;
	static QHash<QString, QByteArray> cache;
	static QQueue<QString> cacheOrder;

	static QString RandomHex(int bytes)
	{
		QByteArray data(bytes, 0);
		for (int i = 0; i < bytes; i++)
			data[i] = (char)QRandomGenerator::system()->bounded(256);
		return QString::fromLatin1(data.toHex());
	}

	static QString Directory()
	{
		QString dir = qEnvironmentVariable("MINEIMATOR_LIVE_DIR");
		if (dir.isEmpty())
			dir = QStandardPaths::writableLocation(QStandardPaths::GenericDataLocation) + "/Mine-imator/live";
		return QDir::cleanPath(QDir::fromNativeSeparators(dir));
	}

	static bool TokenMatches(const QByteArray& given)
	{
		if (given.size() != token.size())
			return false;
		char diff = 0;
		for (int i = 0; i < token.size(); i++)
			diff |= given[i] ^ token[i];
		return diff == 0;
	}

	static QByteArray Line(QJsonObject response, const QString& requestId)
	{
		response["schema"] = "mineimator.live/1";
		response["sessionId"] = sessionId;
		response["requestId"] = requestId.isEmpty() ? QJsonValue() : QJsonValue(requestId);
		return QJsonDocument(response).toJson(QJsonDocument::Compact) + "\n";
	}

	static QJsonObject Failure(const QString& code, const QString& message)
	{
		QJsonObject error;
		error["code"] = code;
		error["message"] = message;
		QJsonObject response;
		response["status"] = "error";
		response["error"] = error;
		return response;
	}

	static void Send(const QList<QPointer<QTcpSocket>>& sockets, const QByteArray& line)
	{
		for (const QPointer<QTcpSocket>& socket : sockets)
			if (socket && socket->state() == QAbstractSocket::ConnectedState)
				socket->write(line);
	}

	static void Remember(const QString& requestId, const QByteArray& line)
	{
		if (requestId.isEmpty())
			return;
		cache[requestId] = line;
		cacheOrder.enqueue(requestId);
		while (cacheOrder.size() > MAX_CACHE)
			cache.remove(cacheOrder.dequeue());
	}

	static Request* Pending(const QString& requestId)
	{
		if (hasCurrent && current.requestId == requestId)
			return &current;
		for (Request& request : queue)
			if (request.requestId == requestId)
				return &request;
		return nullptr;
	}

	static void Receive(QTcpSocket* socket, const QByteArray& line)
	{
		QJsonParseError parseError;
		QJsonDocument doc = QJsonDocument::fromJson(line, &parseError);
		if (parseError.error != QJsonParseError::NoError || !doc.isObject())
		{
			socket->write(Line(Failure("INVALID_REQUEST", "Each request must be one JSON object on one line"), ""));
			return;
		}

		QJsonObject obj = doc.object();
		if (!obj.value("token").isString() || !TokenMatches(obj.value("token").toString().toUtf8()))
		{
			socket->write(Line(Failure("UNAUTHORIZED", "Missing or wrong token, read it from the session file"), ""));
			socket->disconnectFromHost();
			return;
		}
		obj.remove("token");

		QString requestId;
		if (obj.contains("requestId"))
		{
			if (!obj.value("requestId").isString() || obj.value("requestId").toString().isEmpty() || obj.value("requestId").toString().size() > 128)
			{
				socket->write(Line(Failure("INVALID_REQUEST", "requestId must be a string of 1 to 128 characters"), ""));
				return;
			}
			requestId = obj.value("requestId").toString();
			obj.remove("requestId");

			if (cache.contains(requestId))
			{
				socket->write(cache[requestId]);
				return;
			}
			if (Request* pending = Pending(requestId))
			{
				pending->waiters.append(socket);
				return;
			}
		}

		if (queue.size() >= MAX_QUEUE)
		{
			socket->write(Line(Failure("EDITOR_BUSY", "Too many requests are waiting"), requestId));
			return;
		}

		Request request;
		request.requestId = requestId;
		request.body = QJsonDocument(obj).toJson(QJsonDocument::Compact);
		request.received = QDateTime::currentMSecsSinceEpoch();
		request.waiters.append(socket);
		queue.enqueue(request);
	}

	static void Connect()
	{
		while (server && server->hasPendingConnections())
		{
			QTcpSocket* socket = server->nextPendingConnection();
			QObject::connect(socket, &QTcpSocket::disconnected, socket, &QObject::deleteLater);
			QObject::connect(socket, &QTcpSocket::readyRead, socket, [socket]()
			{
				while (socket->canReadLine())
				{
					QByteArray line = socket->readLine(MAX_LINE + 1).trimmed();
					if (!line.isEmpty())
						Receive(socket, line);
				}
				if (socket->bytesAvailable() > MAX_LINE)
				{
					socket->write(Line(Failure("INVALID_REQUEST", "Request is larger than 8 MB"), ""));
					socket->disconnectFromHost();
				}
			});
		}
	}

	void Stop()
	{
		if (!server)
			return;

		for (QTcpSocket* socket : server->findChildren<QTcpSocket*>())
			socket->abort();
		server->close();
		delete server;
		server = nullptr;

		QFile::remove(connectionFile);
		queue.clear();
		hasCurrent = false;
		cache.clear();
		cacheOrder.clear();
	}

	static bool Start()
	{
		if (server)
			return true;

		server = new QTcpServer();
		if (!server->listen(QHostAddress::LocalHost, 0))
		{
			WARNING("Live session could not listen: " + server->errorString());
			delete server;
			server = nullptr;
			return false;
		}
		QObject::connect(server, &QTcpServer::newConnection, server, &Connect);

		sessionId = RandomHex(8);
		token = RandomHex(32).toUtf8();

		QDir dir(Directory());
		connectionFile = dir.filePath(sessionId + ".json");

		QJsonObject info;
		info["schema"] = "mineimator.live-session/1";
		info["sessionId"] = sessionId;
		info["host"] = "127.0.0.1";
		info["port"] = server->serverPort();
		info["token"] = QString::fromUtf8(token);
		info["pid"] = QCoreApplication::applicationPid();
		info["started"] = QDateTime::currentDateTimeUtc().toString(Qt::ISODate);

		QSaveFile file(connectionFile);
		bool written = dir.mkpath(".") && file.open(QIODevice::WriteOnly) &&
			file.setPermissions(QFileDevice::ReadOwner | QFileDevice::WriteOwner) &&
			file.write(QJsonDocument(info).toJson()) > 0 && file.commit();
		if (!written)
		{
			WARNING("Live session could not write " + connectionFile);
			Stop();
			return false;
		}

		static bool hooked = false;
		if (!hooked)
		{
			QObject::connect(qApp, &QCoreApplication::aboutToQuit, &Stop);
			hooked = true;
		}

		DEBUG("Live session " + sessionId + " on 127.0.0.1:" + NumStr(server->serverPort()));
		return true;
	}
}

namespace CppProject
{
	BoolType live_server_start()
	{
		return Live::Start();
	}

	void live_server_stop()
	{
		Live::Stop();
	}

	StringType live_server_session()
	{
		return Live::sessionId;
	}

	IntType live_request_next(BoolType ready, IntType typeMap)
	{
		if (Live::hasCurrent)
		{
			Live::Send(Live::current.waiters, Live::Line(Live::Failure("INTERNAL", "The request got no reply"), Live::current.requestId));
			Live::hasCurrent = false;
		}

		if (!ready)
		{
			qint64 now = QDateTime::currentMSecsSinceEpoch();
			while (!Live::queue.isEmpty() && now - Live::queue.head().received > Live::BUSY_MS)
			{
				Live::Request request = Live::queue.dequeue();
				Live::Send(request.waiters, Live::Line(Live::Failure("EDITOR_BUSY",
					"The editor stayed busy for 10 seconds (dragging, typing, a menu or popup, loading or exporting). Nothing was changed"), request.requestId));
			}
			return -1;
		}

		if (Live::queue.isEmpty())
			return -1;

		Live::current = Live::queue.dequeue();
		Live::hasCurrent = true;
		return json_load_from_string(QString::fromUtf8(Live::current.body), typeMap);
	}

	void live_request_reply(StringType json)
	{
		if (!Live::hasCurrent)
			return;
		Live::hasCurrent = false;

		QJsonParseError parseError;
		QJsonDocument doc = QJsonDocument::fromJson(json.QStr().toUtf8(), &parseError);
		QJsonObject response = (parseError.error == QJsonParseError::NoError && doc.isObject()) ? doc.object() :
			Live::Failure("INTERNAL", "The editor produced an invalid reply");

		QByteArray line = Live::Line(response, Live::current.requestId);
		Live::Send(Live::current.waiters, line);
		Live::Remember(Live::current.requestId, line);
	}
}
