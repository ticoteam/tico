"""A CloudWatch client that keeps each put_metric_data payload, the bytes AWS would receive."""


class CloudWatch:
    def __init__(self):
        self.calls = []

    def put_metric_data(self, **payload):
        self.calls.append(payload)

    def points(self, name):
        """The values sent for one metric, in the order they were sent."""
        return [datum["Value"] for call in self.calls for datum in call["MetricData"] if datum["MetricName"] == name]
